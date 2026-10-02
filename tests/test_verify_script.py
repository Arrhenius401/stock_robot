"""检查入口的范围边界：本地提交不能隐式执行全量测试。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="没有 PowerShell，无法运行 Windows 检查入口")


def _run(*arguments: str):
    assert POWERSHELL is not None
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(REPOSITORY / "scripts/verify.ps1"), *arguments],
        cwd=REPOSITORY, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, check=False,
    )


@pytest.mark.parametrize("path,expected", [
    ("src/stock_robot/cli.py", "tests/test_cli.py"),
    ("config/default.yaml", "tests/utils"),
    ("scripts/verify.ps1", "tests/test_verify_script.py"),
    ("src/radar/calendar.py", "tests/radar"),
    ("pyproject.toml", "tests/test_integration.py"),
    ("tests/api/deleted_test.py", "tests/api"),
    ("tests/resources/index/csi-930740-price-pe-history.json", "tests/index"),
    ("tests/resources/index/cni-980092-valuation-factsheet-layout.txt", "tests/index"),
])
def test_changed_scope_never_expands_to_full(path, expected):
    result = _run("-PlanOnly", "-Files", path)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["Scope"] == "Changed"
    targets = [item.replace("\\", "/") for item in plan["TestTargets"]]
    assert expected in targets
    assert "tests" not in targets
    assert "." not in plan["PythonTargets"]


def test_full_scope_is_explicit():
    result = _run("-PlanOnly", "-Scope", "Full")
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["TestTargets"] == ["tests"]
    assert plan["PythonTargets"] == ["."]


def test_docs_only_check_does_not_require_virtual_environment():
    result = _run("-Files", "AGENTS.md", "-VenvPath", "tmp/nonexistent-check-runtime")
    assert result.returncode == 0, result.stderr


def test_unknown_code_requires_manual_scope_instead_of_full():
    result = _run("-Files", "src/unknown_module/example.py")
    assert result.returncode == 2, result.stderr



def test_explicit_shared_runtime_is_visible_in_plan():
    runtime = str(Path(sys.executable).parent.parent)
    result = _run("-PlanOnly", "-Files", "scripts/verify-runtime.py", "-VenvPath", runtime)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert Path(plan["SourceRoot"]).resolve() == REPOSITORY.resolve()
    assert Path(plan["RuntimeRoot"]).resolve() == Path(runtime).resolve()
    assert "tests/test_verify_script.py" in plan["TestTargets"]
    assert "scripts/verify-runtime.py" in plan["PythonTargets"]


def test_probe_has_bounded_test_mapping():
    result = _run("-PlanOnly", "-Files", "scripts/probe-index-sources.py")
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["UnmappedFiles"] == []
    assert "tests/index/test_source_probe.py" in plan["TestTargets"]


def test_runtime_config_preserves_settings_and_binds_worktree(tmp_path):
    source = tmp_path / "src"
    package = source / "index"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pyright]\ninclude=["src"]\nexclude=["tmp"]\ntypeCheckingMode="basic"\nreportMissingImports=false\n',
        encoding="utf-8",
    )
    output = tmp_path / "tmp" / "pytest" / "unique-run" / "pyrightconfig.json"
    output.parent.mkdir(parents=True)
    env = dict(os.environ, PYTHONPATH=str(source))
    result = subprocess.run(
        [sys.executable, str(REPOSITORY / "scripts/verify-runtime.py"), "--root", str(tmp_path),
         "--runtime", str(Path(sys.executable).parent.parent), "--output", str(output)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(output.read_text(encoding="utf-8"))
    assert config["extraPaths"][0] == str(source.resolve())
    assert config["include"] == ["../../../src"]
    assert (output.parent / config["exclude"][0]).resolve() == (tmp_path / "tmp").resolve()
    assert config["typeCheckingMode"] == "basic"
    assert config["reportMissingImports"] is False
    (package / "marker.py").write_text('value: int = "worktree-error"\n', encoding="utf-8")
    pyright = Path(sys.executable).parent / "pyright.exe"
    checked = subprocess.run([str(pyright), "--project", str(output)], env=env,
                             cwd=tmp_path, capture_output=True, text=True, timeout=20, check=False)
    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "marker.py" in checked.stdout
    assert "Ignoring path" not in checked.stderr + checked.stdout
    # 即使主仓库 editable 安装存在，缺少工作树路径仍必须拒绝。
    env["PYTHONPATH"] = ""
    rejected = subprocess.run(result.args, env=env, cwd=tmp_path, capture_output=True,
                              text=True, timeout=20, check=False)
    assert rejected.returncode != 0
    assert "源码导入未指向当前工作树" in rejected.stderr



def test_directory_failure_restores_callers_pythonpath():
    assert POWERSHELL is not None
    script = str(REPOSITORY / "scripts/verify.ps1").replace("'", "''")
    command = (
        "$env:PYTHONPATH='caller-sentinel'; function New-Item { throw 'injected-directory-failure' }; "
        f"try {{ & '{script}' -Files tests/test_verify_script.py }} catch {{ $failure=$_.Exception.Message }}; "
        "@{Restored=$env:PYTHONPATH; Failure=$failure} | ConvertTo-Json -Compress"
    )
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
                            cwd=REPOSITORY, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout.splitlines()[-1])
    assert outcome["Failure"] == "injected-directory-failure"
    assert outcome["Restored"] == "caller-sentinel"
