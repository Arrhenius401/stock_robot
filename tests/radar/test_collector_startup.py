"""采集服务系统启动模板与平台边界。"""

import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from radar.collector_startup import CollectorStartup, StartupError


def test_windows_template_uses_logon_fixed_paths_and_restart(tmp_path):
    startup = CollectorStartup(tmp_path, python=Path('C:/Python/python.exe'), platform='win32')
    root = ET.fromstring(startup.windows_xml('DOMAIN\\user'))
    ns = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}

    def value(path):
        node = root.find(path, ns)
        assert node is not None
        assert node.text is not None
        return node.text

    assert value('t:Triggers/t:LogonTrigger/t:UserId') == 'DOMAIN\\user'
    assert value('.//t:WorkingDirectory') == str(tmp_path.resolve())
    assert value('.//t:ExecutionTimeLimit') == 'PT0S'
    assert value('.//t:RestartOnFailure/t:Interval') == 'PT1M'
    assert value('.//t:MultipleInstancesPolicy') == 'IgnoreNew'
    assert '--supervise radar daemon' in value('.//t:Arguments')


def test_task_name_is_project_specific(tmp_path):
    assert CollectorStartup(tmp_path).name != CollectorStartup(tmp_path / 'other').name


def test_linux_unit_escapes_paths_and_requires_regular_user(tmp_path):
    startup = CollectorStartup(tmp_path / 'space % folder', python=Path('/srv/venv/bin/python'), platform='linux')
    unit = startup.linux_unit('stockrobot')
    assert 'User=stockrobot' in unit
    assert 'Restart=on-failure' in unit
    assert f'WorkingDirectory={str(startup.project).replace(chr(37), chr(37)*2)}\n' in unit
    assert 'WorkingDirectory="' not in unit
    assert '%%' in unit
    assert 'radar daemon' in unit
    assert '--supervise' not in unit
    with pytest.raises(ValueError):
        startup.linux_unit('root')
    with pytest.raises(ValueError):
        startup.linux_unit('user\nExecStart=bad')


def test_linux_web_changes_are_read_only(tmp_path):
    startup = CollectorStartup(tmp_path, platform='linux')
    with pytest.raises(StartupError, match='部署'):
        startup.set_enabled(True)


def test_linux_status_queries_real_enabled_state(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if 'is-enabled' in command:
            return subprocess.CompletedProcess(command, 0, 'enabled\n', '')
        return subprocess.CompletedProcess(command, 0, 'active\n', '')

    status = CollectorStartup(tmp_path, platform='linux', runner=runner).status()
    assert status['enabled'] is True
    assert status['active'] is True
    assert status['editable'] is False
    assert len(calls) == 2


def test_startup_status_never_claims_online_from_registration(tmp_path):
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, '<Task><Settings><Enabled>true</Enabled></Settings></Task>', '')

    status = CollectorStartup(tmp_path, platform='win32', runner=runner).status()
    assert status['registered'] is True
    assert status['active'] is None


def test_timeout_is_visible(tmp_path):
    def runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 15)

    status = CollectorStartup(tmp_path, platform='linux', runner=runner).status()
    assert status['error']
    assert status['enabled'] is None


def test_windows_and_linux_bootstrap_are_absolute(tmp_path):
    startup = CollectorStartup(tmp_path, python=Path('C:/Python/python.exe'), platform='win32')
    root = ET.fromstring(startup.windows_xml('user'))
    arguments = root.find('.//{*}Arguments')
    assert arguments is not None
    assert arguments.text is not None
    assert str(tmp_path / 'scripts' / 'run-radar-collector.py') in arguments.text
    assert '-m stock_robot.cli' not in arguments.text
    assert str(tmp_path / 'scripts' / 'run-radar-collector.py').replace('\\', '\\\\') in startup.linux_unit('stockrobot')


def test_project_bootstrap_ignores_editable_other_project(tmp_path):
    import json
    import sys

    project = tmp_path / 'project'
    scripts = project / 'scripts'
    scripts.mkdir(parents=True)
    source = project / 'src' / 'stock_robot'
    source.mkdir(parents=True)
    (source / '__init__.py').write_text('', encoding='utf-8')
    (source / 'cli.py').write_text('import json,os,sys\ndef main():\n print(json.dumps([os.getcwd(),__file__,sys.argv[1:]]))\n', encoding='utf-8')
    launcher = scripts / 'run-radar-collector.py'
    launcher.write_text((Path(__file__).parents[2] / 'scripts/run-radar-collector.py').read_text(encoding='utf-8'), encoding='utf-8')
    result = subprocess.run([sys.executable, str(launcher), 'radar', 'daemon'], cwd=tmp_path, capture_output=True, text=True, check=True, timeout=10)
    working, module, arguments = json.loads(result.stdout)
    assert working == str(project)
    assert module == str(source / 'cli.py')
    assert arguments == ['radar', 'daemon']


def test_legacy_migration_never_changes_other_project(tmp_path):
    import json

    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        if '/XML' in command:
            xml = '<Task><Actions><Exec><Command>C:\\other\\.venv\\Scripts\\stock-robot.exe</Command><Arguments>radar collect</Arguments></Exec></Actions></Task>'
            return subprocess.CompletedProcess(command, 0, xml, '')
        return subprocess.CompletedProcess(command, 0, json.dumps([{'ProcessId': 42, 'CommandLine': 'C:\\other\\.venv\\Scripts\\stock-robot.exe radar collect'}]), '')
    startup = CollectorStartup(tmp_path, platform='win32', runner=runner)
    directory = tmp_path / 'startup'
    directory.mkdir()
    launcher = directory / 'stock-robot-radar-collector.cmd'
    launcher.write_text('@echo off\nstart "" /b /d "C:\\other" C:\\other\\.venv\\Scripts\\stock-robot.exe radar collect --hour 18 --minute 30\n', encoding='utf-8')
    result = startup.migrate_legacy(state_dir=tmp_path / 'state', startup_directory=directory)
    assert not result['cleanup_pending']
    assert not result['task_matched']
    assert not result['launcher_matched']
    assert result['stopped_process_ids'] == []
    assert launcher.exists()
    assert not any('/Disable' in command or '/Delete' in command for command in calls)


def test_legacy_matching_launcher_cleanup_failure_retains_marker(tmp_path, monkeypatch):
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, '', 'ERROR: cannot find the file specified') if '/Query' in command else subprocess.CompletedProcess(command, 0, '[]', '')
    startup = CollectorStartup(tmp_path, platform='win32', runner=runner)
    directory = tmp_path / 'startup'
    directory.mkdir()
    launcher = directory / 'stock-robot-radar-collector.cmd'
    executable = tmp_path / '.venv/Scripts/stock-robot.exe'
    launcher.write_text(f'@echo off\nstart "" /b /d "{tmp_path}" "{executable}" radar collect --hour 18 --minute 30\n', encoding='utf-8')
    original_unlink = Path.unlink
    def blocked(path, missing_ok=False):
        if path == launcher:
            raise PermissionError('startup directory denied')
        return original_unlink(path, missing_ok=missing_ok)
    monkeypatch.setattr(Path, 'unlink', blocked)
    result = startup.migrate_legacy(state_dir=tmp_path / 'state', startup_directory=directory)
    assert result['launcher_matched']
    assert result['cleanup_pending']
    assert (tmp_path / 'state/radar_collector.disabled').exists()
    assert result['errors']



def test_matching_legacy_task_and_process_are_migrated(tmp_path):
    import json

    calls = []
    executable = tmp_path / '.venv/Scripts/stock-robot.exe'
    def runner(command, **kwargs):
        calls.append(command)
        if '/XML' in command:
            xml = f'<Task><Actions><Exec><Command>{executable}</Command><Arguments>radar collect --hour 18 --minute 30</Arguments><WorkingDirectory>{tmp_path}</WorkingDirectory></Exec></Actions></Task>'
            return subprocess.CompletedProcess(command, 0, xml, '')
        if command[0] == 'powershell.exe' and 'ConvertTo-Json' in command[-1]:
            return subprocess.CompletedProcess(command, 0, json.dumps([{'ProcessId': 42, 'CommandLine': f'"{executable}" radar collect --hour 18 --minute 30'}]), '')
        return subprocess.CompletedProcess(command, 0, '', '')
    startup = CollectorStartup(tmp_path, platform='win32', runner=runner, process_cwd=lambda pid: tmp_path)
    result = startup.migrate_legacy(state_dir=tmp_path / 'state', startup_directory=tmp_path / 'startup')
    assert result['task_matched']
    assert not result['cleanup_pending']
    assert result['stopped_process_ids'] == [42]
    assert any('/Disable' in command for command in calls)
    assert any(command[0] == 'powershell.exe' and 'Stop-Process -Id 42' in command[-1] for command in calls)
    assert result['marker_retained']



def test_linux_absolute_paths_do_not_expand_environment_variables(tmp_path):
    startup = CollectorStartup(tmp_path / '${USER}', python=Path('/srv/$python/bin/python'), platform='linux')
    unit = startup.linux_unit('stockrobot')
    assert 'ExecStart=:' in unit
    assert '${USER}' in unit


def test_windows_registration_failure_cleans_xml_without_running_task(tmp_path):
    import sys

    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/run-radar-collector.py').write_text('', encoding='utf-8')
    files = []
    commands = []
    def runner(command, **kwargs):
        commands.append(command)
        if '/XML' in command:
            files.append(Path(command[command.index('/XML')+1]))
        return subprocess.CompletedProcess(command, 1, '', 'Access denied')
    startup = CollectorStartup(tmp_path, python=Path(sys.executable), platform='win32', runner=runner)
    with pytest.raises(StartupError, match='Access denied'):
        startup.set_enabled(True)
    assert files and not files[0].exists()
    assert not any('/Run' in command for command in commands)



def test_legacy_process_with_shared_venv_but_other_cwd_is_not_stopped(tmp_path):
    import json

    calls = []
    executable = tmp_path / '.venv/Scripts/stock-robot.exe'
    def runner(command, **kwargs):
        calls.append(command)
        if '/Query' in command:
            return subprocess.CompletedProcess(command, 1, '', 'ERROR: cannot find the file specified')
        return subprocess.CompletedProcess(command, 0, json.dumps([{'ProcessId':42,'CommandLine':f'"{executable}" radar collect'}]), '')
    startup = CollectorStartup(tmp_path, platform='win32', runner=runner, process_cwd=lambda pid: tmp_path / 'another-project')
    result = startup.migrate_legacy(state_dir=tmp_path / 'state', startup_directory=tmp_path / 'startup')
    assert result['stopped_process_ids'] == []
    assert not any('Stop-Process' in command[-1] for command in calls)
    assert result['cleanup_pending'] is False


def test_unverifiable_legacy_cwd_is_audited_and_never_stopped(tmp_path):
    import json

    calls = []
    executable = tmp_path / '.venv/Scripts/stock-robot.exe'
    def runner(command, **kwargs):
        calls.append(command)
        if '/Query' in command:
            return subprocess.CompletedProcess(command, 1, '', 'ERROR: cannot find the file specified')
        return subprocess.CompletedProcess(command, 0, json.dumps([{'ProcessId':42,'CommandLine':f'"{executable}" radar collect'}]), '')
    def denied(pid):
        raise StartupError('无法核验工作目录')
    startup = CollectorStartup(tmp_path, platform='win32', runner=runner, process_cwd=denied)
    result = startup.migrate_legacy(state_dir=tmp_path / 'state', startup_directory=tmp_path / 'startup')
    assert result['cleanup_pending']
    assert result['unsafe_overlap']
    assert result['active_legacy_process_ids'] == [42]
    assert not any('Stop-Process' in command[-1] for command in calls)
    assert (tmp_path / 'state/radar_collector.disabled').is_file()



def test_global_process_verification_failure_blocks_cutover(tmp_path):
    def runner(command, **kwargs):
        detail = 'ERROR: cannot find the file specified' if '/Query' in command else 'CIM access denied'
        return subprocess.CompletedProcess(command, 1, '', detail)
    startup = CollectorStartup(tmp_path, platform='win32', runner=runner)
    result = startup.migrate_legacy(state_dir=tmp_path / 'state', startup_directory=tmp_path / 'startup')
    assert result['cleanup_pending']
    assert result['process_verification_failed']
    assert result['unsafe_overlap']


def test_marker_write_failure_with_known_legacy_process_blocks_cutover(tmp_path, monkeypatch):
    import json

    executable = tmp_path / '.venv/Scripts/stock-robot.exe'
    def runner(command, **kwargs):
        if '/Query' in command:
            return subprocess.CompletedProcess(command, 1, '', 'ERROR: cannot find the file specified')
        return subprocess.CompletedProcess(command, 0, json.dumps([{'ProcessId':42,'CommandLine':f'"{executable}" radar collect'}]), '')
    original = Path.write_text
    def blocked(path, *args, **kwargs):
        if path.name == 'radar_collector.disabled':
            raise PermissionError('state directory denied')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',blocked)
    startup = CollectorStartup(tmp_path, platform='win32', runner=runner, process_cwd=lambda pid: tmp_path)
    result = startup.migrate_legacy(state_dir=tmp_path / 'state', startup_directory=tmp_path / 'startup')
    assert result['cleanup_pending']
    assert result['active_legacy_process_ids'] == [42]
    assert result['unsafe_overlap']


@pytest.mark.skipif(__import__('sys').platform != 'win32', reason='Windows 监督进程')
def test_supervisor_restarts_failure_and_stops_on_success(tmp_path):
    import json
    import sys

    child = tmp_path / 'child.py'
    proof = tmp_path / 'proof.json'
    child.write_text("import json,sys\nfrom pathlib import Path\np=Path(sys.argv[1])\nn=json.loads(p.read_text())+1 if p.exists() else 1\np.write_text(json.dumps(n))\nraise SystemExit(1 if n == 1 else 0)\n", encoding='utf-8')
    launcher = Path(__file__).parents[2] / 'scripts/run-radar-collector.py'
    entry = tmp_path / 'entry.py'
    entry.write_text("import importlib.util,sys\ns=importlib.util.spec_from_file_location('bootstrap',sys.argv[1])\nm=importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\nraise SystemExit(m.supervise(sys.argv[2:],restart_delay=.1))\n", encoding='utf-8')
    result = subprocess.run([sys.executable, str(entry), str(launcher), sys.executable, str(child), str(proof)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(proof.read_text()) == 2
    assert '15' not in result.stderr


def test_native_legacy_enumeration_avoids_cim_and_ignores_other_projects(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import psutil

    startup = CollectorStartup(tmp_path, platform='win32')
    matching = SimpleNamespace(pid=42, info={'name':'python.exe'}, cmdline=lambda:[str(startup.legacy_executable),'radar','collect'])
    other = SimpleNamespace(pid=43, info={'name':'python.exe'}, cmdline=lambda:['C:/other/stock-robot.exe','radar','collect'])
    unrelated = SimpleNamespace(pid=44, info={'name':'browser.exe'})
    monkeypatch.setattr(psutil,'process_iter',lambda attrs:iter([matching,other,unrelated]))
    monkeypatch.setattr(startup,'_powershell',lambda script:pytest.fail('原生查询不应执行 CIM'))
    assert startup._legacy_process_rows() == [{'ProcessId':42,'CommandLine':subprocess.list2cmdline(matching.cmdline())}]


def test_native_legacy_stop_rechecks_current_command(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import psutil

    startup = CollectorStartup(tmp_path, platform='win32')
    process = SimpleNamespace(cmdline=lambda:['C:/other/python.exe','radar','collect'],terminate=lambda:pytest.fail('不能结束已改变的进程'))
    monkeypatch.setattr(psutil,'Process',lambda pid:process)
    with pytest.raises(StartupError,match='命令已改变'):
        startup._stop_legacy_process(42)


@pytest.mark.parametrize('error', ['ERROR: The system cannot find the file specified.', 'ERROR: The system cannot find the path specified.', '错误: 找不到指定的文件。'])
def test_first_windows_startup_missing_task_can_be_enabled(tmp_path, error):
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, '', error)
    status = CollectorStartup(tmp_path, platform='win32', runner=runner).status()
    assert status['registered'] is False
    assert status['enabled'] is False
    assert status['editable'] is True
    assert status['error'] is None
    assert status['active'] is None


@pytest.mark.parametrize('error', ['ERROR: Access is denied.', 'RPC server is unavailable.', ''])
def test_windows_startup_unknown_failure_remains_unknown(tmp_path, error):
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, '', error)
    status = CollectorStartup(tmp_path, platform='win32', runner=runner).status()
    assert status['enabled'] is None
    assert status['error']


def test_linux_virtualenv_interpreter_does_not_follow_symlink(tmp_path):
    interpreter = tmp_path / 'system-python'
    interpreter.write_text('', encoding='utf-8')
    venv_python = tmp_path / '.venv/bin/python'
    venv_python.parent.mkdir(parents=True)
    try:
        venv_python.symlink_to(interpreter)
    except OSError as exc:
        pytest.skip(f'当前账户不能创建符号链接：{exc}')
    startup = CollectorStartup(tmp_path, python=venv_python, platform='linux')
    assert startup.python == venv_python.absolute()
    assert startup.python != venv_python.resolve()
    unit = startup.linux_unit('stockrobot')
    expected = str(venv_python.absolute()).replace('\\', '\\\\')
    assert f'ExecStart=:"{expected}" ' in unit
    assert str(interpreter) not in unit


def test_interpreter_path_is_never_resolved_to_system_python(tmp_path, monkeypatch):
    venv_python = tmp_path / '.venv/bin/python'
    original_resolve = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == venv_python:
            pytest.fail('启动模板不应跟随虚拟环境解释器链接')
        return original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', resolve)
    startup = CollectorStartup(tmp_path, python=venv_python, platform='linux')
    assert startup.python == venv_python.absolute()
    assert str(startup.python).replace('\\', '\\\\') in startup.linux_unit('stockrobot')
