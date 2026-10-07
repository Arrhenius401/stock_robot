"""旧采集设置迁移和 YAML 跨实例读取回归。"""
import sqlite3

from radar.collector_store import CollectorStore
from utils.config import Config


def legacy_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript("CREATE TABLE collector_settings(singleton INTEGER PRIMARY KEY, enabled INTEGER, hour INTEGER, minute INTEGER, revision INTEGER); INSERT INTO collector_settings VALUES(1,1,19,45,7);")


def test_legacy_settings_migrate_when_no_yaml_exists(tmp_path):
    database = tmp_path / "radar_collector.db"
    legacy_database(database)
    config = Config(config_dir=tmp_path)
    assert config.get("radar.collector") == {"enabled": True, "hour": 19, "minute": 45}
    assert CollectorStore(database).settings()["hour"] == 19


def test_partial_yaml_wins_and_changes_cancel_only_automatic(tmp_path):
    database = tmp_path / "radar_collector.db"
    legacy_database(database)
    (tmp_path / "config.yaml").write_text("radar:\n  collector:\n    hour: 20\n", encoding="utf-8")
    store = CollectorStore(database)
    assert store.settings()["hour"] == 20
    assert store.settings()["minute"] == 45
    auto = store.enqueue("auto", "2026-10-01", "a", "automatic", ["a"])
    manual = store.enqueue("manual", "2026-10-01", "b", "manual", ["b"])
    Config(config_dir=tmp_path).update({"radar": {"collector": {"enabled": False}}})
    assert not store.settings()["enabled"]
    assert store.get_run(auto["id"])["status"] == "cancelled"
    assert store.get_run(manual["id"])["status"] == "queued"


def test_unrelated_save_migrates_before_defaults_and_deleted_values_do_not_return(tmp_path):
    database = tmp_path / "radar_collector.db"
    legacy_database(database)
    (tmp_path / "config.yaml").write_text("llm:\n  model: old\n", encoding="utf-8")
    config = Config(config_dir=tmp_path)
    config.update({"llm": {"model": "new"}})
    assert CollectorStore(database).settings()["enabled"] is True
    config.replace_source("llm:\n  model: new\n", {"llm": {"model": "new"}}, config.revision)
    assert CollectorStore(database).settings()["enabled"] is False
