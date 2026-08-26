"""项目本地状态目录测试。"""
from utils.paths import project_state_dir


def test_project_state_dir_is_created_under_current_directory(monkeypatch, tmp_path):
    """默认状态目录固定在执行时的当前项目目录。"""
    monkeypatch.chdir(tmp_path)

    assert project_state_dir() == tmp_path / ".stock_robot"
    assert project_state_dir().is_dir()
