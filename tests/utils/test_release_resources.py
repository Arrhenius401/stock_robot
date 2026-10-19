"""源码与安装包的资源必须独立于当前工作目录。"""
from pathlib import Path

from utils import paths


def test_source_resource_root_is_repository(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    root = paths.resource_root()
    assert root == Path(__file__).resolve().parents[2]
    assert (root / "data/index_mapping.csv").is_file()


def test_installed_resources_are_read_only_defaults(monkeypatch, tmp_path):
    bundle = tmp_path / "site-packages/stock_robot/resources"
    (bundle / "data").mkdir(parents=True)
    original = bundle / "data/industry_mapping.csv"
    original.write_text("内置数据", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(paths, "_source_root", lambda: None)
    monkeypatch.setattr(paths, "_bundled_root", lambda: bundle)
    assert paths.resource_root() == bundle
    assert paths.runtime_root() == workspace
    assert paths.industry_mapping_path() == original
    writable = paths.industry_mapping_path(writable=True)
    assert writable == workspace / ".stock_robot/data/industry_mapping.csv"
    assert writable.read_text(encoding="utf-8") == "内置数据"
    writable.write_text("用户更新", encoding="utf-8")
    assert paths.industry_mapping_path() == writable
    assert original.read_text(encoding="utf-8") == "内置数据"


def test_source_mapping_remains_at_existing_path():
    assert paths.industry_mapping_path(writable=True) == paths.resource_root() / "data/industry_mapping.csv"
