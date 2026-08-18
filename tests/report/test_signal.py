"""信号推导与配置加载测试"""
import pytest

from report.signal import (
    SIGNAL_LABELS,
    derive_signal,
    load_signal_config,
)
from utils.config import Config


class TestDeriveSignal:
    def test_threshold_boundaries(self):
        # 默认阈值：attack=7, watch=4
        thresholds = {"attack": 7.0, "watch": 4.0}
        assert derive_signal(7.0, thresholds) == "attack"
        assert derive_signal(6.9, thresholds) == "watch"
        assert derive_signal(4.0, thresholds) == "watch"
        assert derive_signal(3.9, thresholds) == "defend"
        assert derive_signal(0.0, thresholds) == "defend"
        assert derive_signal(10.0, thresholds) == "attack"

    def test_custom_thresholds(self):
        thresholds = {"attack": 8.0, "watch": 5.0}
        assert derive_signal(7.5, thresholds) == "watch"
        assert derive_signal(8.0, thresholds) == "attack"
        assert derive_signal(4.9, thresholds) == "defend"


class TestLoadSignalConfig:
    def test_default_config_loads(self, tmp_path):
        cfg = Config(config_dir=tmp_path)
        sc = load_signal_config(cfg)
        assert sc.thresholds == {"attack": 7.0, "watch": 4.0}
        assert set(sc.actions) == {"attack", "watch", "defend"}
        assert sc.actions["attack"].action == "可考虑建仓/加仓"
        assert sc.actions["attack"].position == "60%-80%"

    def test_custom_thresholds_and_actions(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "signal:\n"
            "  thresholds: {attack: 8, watch: 5}\n"
            "  actions:\n"
            "    attack: {action: 强势加仓, position: 70%-90%}\n",
            encoding="utf-8",
        )
        sc = load_signal_config(Config(config_dir=tmp_path))
        assert sc.thresholds == {"attack": 8.0, "watch": 5.0}
        # 部分覆盖：attack 用自定义，watch/defend 保留默认（Config 深合并兜底）
        assert sc.actions["attack"].action == "强势加仓"
        assert sc.actions["watch"].action == "持有观察，等待明确方向"
        assert sc.actions["defend"].position == "0%-20%"

    def test_partial_action_fields_fallback_to_default(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "signal:\n"
            "  actions:\n"
            "    attack: {action: 强势加仓}\n",  # 缺 position → 默认兜底
            encoding="utf-8",
        )
        sc = load_signal_config(Config(config_dir=tmp_path))
        assert sc.actions["attack"].action == "强势加仓"
        assert sc.actions["attack"].position == "60%-80%"

    @pytest.mark.parametrize("yaml_text", [
        "signal: null\n",
        "signal: {thresholds: null}\n",
        "signal: {actions: null}\n",
        "signal: {thresholds: {attack: 4, watch: 7}}\n",  # watch >= attack
        "signal: {thresholds: {attack: 11, watch: 4}}\n",  # attack > 10
        "signal: {thresholds: {attack: 7, watch: 0}}\n",   # watch <= 0
    ])
    def test_invalid_config_raises(self, tmp_path, yaml_text):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_text, encoding="utf-8")
        with pytest.raises(ValueError):
            load_signal_config(Config(config_dir=tmp_path))

    def test_missing_threshold_key_raises(self, tmp_path):
        """缺 watch 阈值键 → ValueError（Config 深合并兜底默认值，缺失键需直接移除模拟）"""
        cfg = Config(config_dir=tmp_path)
        del cfg.data["signal"]["thresholds"]["watch"]
        with pytest.raises(ValueError):
            load_signal_config(cfg)

    def test_missing_action_keys_raises(self, tmp_path):
        """actions 缺 watch/defend 键 → ValueError（Config 深合并兜底默认值，缺失键需直接移除模拟）"""
        cfg = Config(config_dir=tmp_path)
        del cfg.data["signal"]["actions"]["watch"]
        del cfg.data["signal"]["actions"]["defend"]
        with pytest.raises(ValueError):
            load_signal_config(cfg)

    def test_labels_fixed(self):
        assert SIGNAL_LABELS == {"attack": "进攻", "watch": "观望", "defend": "防御"}
