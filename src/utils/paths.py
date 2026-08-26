"""项目本地运行状态路径。"""
from pathlib import Path


def project_state_dir() -> Path:
    """返回并创建当前项目的本地状态目录。"""
    path = Path.cwd() / ".stock_robot"
    path.mkdir(parents=True, exist_ok=True)
    return path
