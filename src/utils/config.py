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
        "retry_times": 2,
        "timeout_seconds": 60,
    },
    "data": {
        "cache_ttl": {
            "daily": 86400,
            "quarterly": 604800,
            "news": 21600,
        },
        "disclaimer_accepted": False,
    },
    "api": {
        "host": "127.0.0.1",
        "port": 25618,
    },
    "push": {
        "enabled": True,
        "max_symbols_per_subscription": 20,
        "email": {
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
            "smtp_user": "",
            "smtp_password": "",
            "to_addr": "",
        },
        "wecom": {
            "corp_id": "",
            "agent_id": "",
            "secret": "",
            "to_user": "@all",
        },
    },
    "signal": {
        "thresholds": {
            "attack": 7,
            "watch": 4,
        },
        "actions": {
            "attack": {"action": "可考虑建仓/加仓", "position": "60%-80%"},
            "watch": {"action": "持有观察，等待明确方向", "position": "30%-50%"},
            "defend": {"action": "减仓或回避", "position": "0%-20%"},
        },
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
