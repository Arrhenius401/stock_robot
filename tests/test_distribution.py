"""构建包的完整性回归，不安装或下载业务依赖。"""
import os
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import zipfile
from pathlib import Path


def test_wheel_and_sdist_contain_runtime_resources(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    # 构建输入只复制声明的源码与资源，排除开发机缓存/凭据。
    for name in ("src", "config", "data"):
        shutil.copytree(root / name, source / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.lock", "*.candidate*"))
    for name in ("pyproject.toml", "MANIFEST.in", "README.md", "requirements-core.lock.txt", "requirements-rag.lock.txt"):
        shutil.copy2(root / name, source / name)
    (source / "scripts").mkdir()
    shutil.copy2(root / "scripts/run-radar-collector.py", source / "scripts")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", "import setuptools.build_meta as b; b.build_wheel('dist'); b.build_sdist('dist')"],
        cwd=source, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheel = next((source / "dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        expected = {
            "api/static/index.html", "api/static/css/app.css", "api/static/js/chat.js",
            "report/templates/report_v2.jinja2", "llm/prompt_templates/summary_openai.jinja2",
            "analysis/config/global_const.yaml", "stock_robot/metadata.py",
            "stock_robot/resources/data/industry_mapping.csv", "stock_robot/resources/data/index_mapping.csv",
            "stock_robot/resources/config/radar_benchmarks.yaml",
            "stock_robot/resources/config/strategies/report_technical.yaml",
            "stock_robot/resources/scripts/run-radar-collector.py",
        }
        assert expected <= names, expected - names
        assert not any(".stock_robot/" in name or "__pycache__" in name for name in names)
    with tarfile.open(next((source / "dist").glob("*.tar.gz"))) as archive:
        names = {name.split("/", 1)[-1] for name in archive.getnames()}
        assert {"src/stock_robot/_build.py", "requirements-core.lock.txt", "requirements-rag.lock.txt", "data/index_mapping.csv", "config/radar_benchmarks.yaml"} <= names
        # 仅解压本测试刚构建的可信本地产物，不处理外部上传归档。
        if hasattr(tarfile, "data_filter"):
            archive.extractall(tmp_path / "sdist", filter="data")
        else:
            # Python 3.11 早期补丁版没有过滤参数；输入仅限上方自产归档。
            archive.extractall(tmp_path / "sdist")
    rebuilt_source = next((tmp_path / "sdist").iterdir())
    rebuilt = subprocess.run(
        [sys.executable, "-c", "import setuptools.build_meta as b; b.build_wheel('dist')"],
        cwd=rebuilt_source, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False,
    )
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    wheel = next((rebuilt_source / "dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert expected <= set(archive.namelist())
    runtime = tmp_path / "env"
    created = subprocess.run([sys.executable, "-m", "venv", str(runtime)], capture_output=True, text=True, timeout=90, check=False)
    assert created.returncode == 0, created.stdout + created.stderr
    executable = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    installed = subprocess.run(
        [str(executable), "-m", "pip", "install", "--no-deps", "--no-index", str(wheel)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=90, check=False,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # 只复用第三方依赖，不执行父环境 .pth，不增加任何仓库源码路径。
    bootstrap = f"import sys; sys.path.append({sysconfig.get_paths()['purelib']!r}); "
    smoke = r'''
import importlib.metadata
from pathlib import Path
import stock_robot.metadata
from stock_robot.metadata import VERSION
from utils.paths import resource_root, runtime_root
from analysis.config_loader import ConfigLoader
from data.index_mapping import IndexMapping, ETFIndexMapping
from data.industry_classifier import IndustryClassifier
from report.builder import ReportBuilder
from radar.universe import UniverseRepository
from api.app import create_app
from fastapi.testclient import TestClient
from radar.collector_startup import CollectorStartup
from xml.etree import ElementTree as ET
assert Path(stock_robot.metadata.__file__).is_relative_to(Path(sys.prefix))
assert Path(importlib.metadata.distribution('stock-robot').locate_file('')).is_relative_to(Path(sys.prefix))
assert importlib.metadata.version('stock-robot') == VERSION
assert runtime_root() == Path.cwd()
assert ConfigLoader().load('银行')
assert len(IndexMapping()) > 0
assert ETFIndexMapping()
assert IndustryClassifier().symbol_count > 0
assert ReportBuilder()._env.get_template('report_v2.jinja2')
assert UniverseRepository(resource_root() / 'config/radar_universes').load_all()
launcher = resource_root() / 'scripts/run-radar-collector.py'
startup = CollectorStartup(Path.cwd())
xml = ET.fromstring(startup.windows_xml('test-user'))
assert str(launcher) in xml.find('.//{*}Arguments').text
assert xml.find('.//{*}WorkingDirectory').text == str(Path.cwd())
unit = startup.linux_unit('stockrobot')
assert str(launcher).replace('\\', '\\\\') in unit
assert 'WorkingDirectory=' + str(Path.cwd()) + '\n' in unit
client = TestClient(create_app())
assert client.get('/health').json()['version'] == VERSION
assert client.get('/').status_code == 200
assert client.get('/js/chat.js').status_code == 200
assert client.get('/api/v1/radar/universes').status_code == 200
print('INSTALLED_WHEEL_SMOKE_OK')
'''
    checked = subprocess.run(
        [str(executable), "-I", "-c", bootstrap + smoke],
        cwd=workspace, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "INSTALLED_WHEEL_SMOKE_OK" in checked.stdout
    command = runtime / ("Scripts/stock-robot.exe" if os.name == "nt" else "bin/stock-robot")
    environment["PYTHONPATH"] = sysconfig.get_paths()["purelib"]
    launcher = runtime / ("Lib/site-packages" if os.name == "nt" else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages") / "stock_robot/resources/scripts/run-radar-collector.py"
    launched = subprocess.run([str(executable), str(launcher), "--help"], cwd=workspace, env=environment, capture_output=True, text=True, timeout=30, check=False)
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert "radar" in launched.stdout
    cli = subprocess.run([str(command), "--version"], cwd=workspace, env=environment, capture_output=True, text=True, timeout=30, check=False)
    assert cli.returncode == 0, cli.stdout + cli.stderr
    from stock_robot.metadata import VERSION

    assert cli.stdout.strip().endswith(VERSION)
