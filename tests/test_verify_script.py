"""检查入口的范围边界：本地提交不能隐式执行全量测试。"""
import json
import shutil
import subprocess
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
