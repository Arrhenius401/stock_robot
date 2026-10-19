"""区分随包只读资源、源码根目录与用户可写运行状态。"""
import os
import shutil
import tempfile
from pathlib import Path

from filelock import FileLock


def _source_root() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").is_file() and (root / "src/stock_robot").is_dir() else None


def _bundled_root() -> Path:
    import stock_robot

    return Path(stock_robot.__file__).parent / "resources"


def resource_root() -> Path:
    """源码沿用原资源，wheel 从包内读取，不依赖启动 cwd。"""
    return _source_root() or _bundled_root()


def runtime_root() -> Path:
    """保留源码雷达产物路径；安装包产物写入用户工作目录。"""
    return _source_root() or Path.cwd()


def industry_mapping_path(*, writable: bool = False) -> Path:
    """安装包映射按需写入状态目录，源码保持既有更新位置。"""
    source = _source_root()
    if source is not None:
        return source / "data/industry_mapping.csv"
    bundled = resource_root() / "data/industry_mapping.csv"
    local = Path.cwd() / ".stock_robot/data/industry_mapping.csv"
    if writable:
        local.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(local) + ".lock", timeout=10):
            if not local.exists():
                descriptor, name = tempfile.mkstemp(dir=local.parent)
                os.close(descriptor)
                try:
                    shutil.copyfile(bundled, name)
                    os.replace(name, local)
                finally:
                    Path(name).unlink(missing_ok=True)
    return local if local.exists() else bundled


def project_state_dir() -> Path:
    """返回并创建当前项目的本地状态目录。"""
    path = Path.cwd() / ".stock_robot"
    path.mkdir(parents=True, exist_ok=True)
    return path
