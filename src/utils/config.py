import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = {
    "llm": {
        "provider": "openai",
        "model": "gpt-4o",
        "enabled": True,
        "api_key": "",
        "base_url": "",
        "temperature": 0.3,
        "max_tokens": 2000,
    },
    "data": {
        "cache_ttl": {
            "daily": 86400,
            "quarterly": 604800,
            "news": 21600,
        },
        "disclaimer_accepted": False,
    },
}


class Config:
    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            config_dir = Path.home() / ".stock_robot"
        self._config_dir = Path(config_dir)
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = self._config_dir / "config.yaml"
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._config_path.exists():
            self._write_default()
            return self._deep_copy(DEFAULT_CONFIG)
        with open(self._config_path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        merged = self._deep_copy(DEFAULT_CONFIG)
        self._merge(merged, user_config)
        return merged

    def _write_default(self):
        with open(self._config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(DEFAULT_CONFIG, f, allow_unicode=True, default_flow_style=False)

    def _persist(self):
        with open(self._config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, default_flow_style=False)

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        node = self.data
        for k in keys:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return default
        return node

    def set(self, key: str, value):
        keys = key.split(".")
        node = self.data
        for k in keys[:-1]:
            if k not in node:
                node[k] = {}
            node = node[k]
        node[keys[-1]] = value
        self._persist()

    def get_llm_config(self) -> dict[str, Any]:
        return {k: v for k, v in self.data["llm"].items() if k != "api_key"}

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    @staticmethod
    def _deep_copy(d: dict) -> dict:
        return copy.deepcopy(d)

    @staticmethod
    def _merge(base: dict, override: dict):
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                Config._merge(base[key], value)
            else:
                base[key] = value
