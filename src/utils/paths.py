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


def _data_file_path(name: str, *, writable: bool = False) -> Path:
    """按具体文件选择本地状态或打包资源，不能只按申万映射是否存在判断。"""
    source = _source_root()
    if source is not None:
        return source / "data" / name
    local = Path.cwd() / ".stock_robot" / "data" / name
    if writable:
        local.parent.mkdir(parents=True, exist_ok=True)
        return local
    bundled = resource_root() / "data" / name
    return local if local.exists() else bundled


def _hsics_data_paths(*, writable: bool = False) -> tuple[Path, Path]:
    """CSV 与 JSON 必须同源成对选择，避免本地只更新一半时混用。"""
    source = _source_root()
    if source is not None:
        base = source / "data"
    else:
        local_dir = Path.cwd() / ".stock_robot" / "data"
        if writable:
            local_dir.mkdir(parents=True, exist_ok=True)
            base = local_dir
        else:
            local_csv = local_dir / "hk_hsics_industry.csv"
            local_json = local_dir / "hk_hsics_industry.snapshot.json"
            base = (local_dir if local_csv.exists() and local_json.exists()
                    else resource_root() / "data")
    return base / "hk_hsics_industry.csv", base / "hk_hsics_industry.snapshot.json"


def hk_stock_connect_path(*, writable: bool = False) -> Path:
    """港股通成员快照；安装包按具体文件回退到打包资源。"""
    return _data_file_path("hk_stock_connect.snapshot.json", writable=writable)


def hk_hsics_industry_path(*, writable: bool = False) -> Path:
    """恒生 HSICS 行业映射 CSV；与快照 JSON 成对选择。"""
    return _hsics_data_paths(writable=writable)[0]


def hk_hsics_snapshot_path(*, writable: bool = False) -> Path:
    """恒生 HSICS 行业快照元数据；与 CSV 成对选择。"""
    return _hsics_data_paths(writable=writable)[1]


def project_state_dir() -> Path:
    """返回并创建当前项目的本地状态目录。"""
    path = Path.cwd() / ".stock_robot"
    path.mkdir(parents=True, exist_ok=True)
    return path
