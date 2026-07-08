import tempfile
from pathlib import Path
from utils.config import Config, DEFAULT_CONFIG


class TestConfig:
    def test_default_config_has_required_sections(self):
        cfg = Config(config_dir=Path("/nonexistent"))
        assert "llm" in cfg.data
        assert "data" in cfg.data
        assert cfg.data["llm"]["enabled"] is True
        assert cfg.data["llm"]["provider"] == "openai"

    def test_load_from_yaml_file(self, tmp_path):
        yaml_content = """
llm:
  provider: claude
  model: claude-opus-4-7
  enabled: false
data:
  cache_ttl: 3600
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)
        cfg = Config(config_dir=tmp_path)
        assert cfg.data["llm"]["provider"] == "claude"
        assert cfg.data["llm"]["model"] == "claude-opus-4-7"
        assert cfg.data["data"]["cache_ttl"] == 3600

    def test_get_returns_nested_value(self):
        cfg = Config(config_dir=Path("/nonexistent"))
        assert cfg.get("llm.provider") == "openai"
        assert cfg.get("nonexistent.key", "fallback") == "fallback"

    def test_set_updates_value_and_persists(self, tmp_path):
        cfg = Config(config_dir=tmp_path)
        cfg.set("llm.provider", "claude")
        assert cfg.get("llm.provider") == "claude"
        cfg2 = Config(config_dir=tmp_path)
        assert cfg2.get("llm.provider") == "claude"

    def test_first_run_creates_default_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        assert not config_file.exists()
        Config(config_dir=tmp_path)
        assert config_file.exists()

    def test_disclaimer_flag_defaults_to_false(self):
        cfg = Config(config_dir=Path("/nonexistent"))
        assert cfg.data["data"]["disclaimer_accepted"] is False

    def test_default_config_has_base_url(self):
        cfg = Config(config_dir=Path("/nonexistent"))
        assert cfg.data["llm"]["base_url"] == ""
