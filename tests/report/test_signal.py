"""信号推导与配置加载测试"""
import pytest

from report.signal import (
    SIGNAL_LABELS,
    Signal,
    derive_signal,
    load_signal_config,
)
from utils.config import Config


class TestDeriveSignal:
    def test_threshold_boundaries(self):
        # 默认阈值：attack=7, watch=4
        thresholds: dict[Signal, float] = {"attack": 7.0, "watch": 4.0}
        assert derive_signal(7.0, thresholds) == "attack"
        assert derive_signal(6.9, thresholds) == "watch"
        assert derive_signal(4.0, thresholds) == "watch"
        assert derive_signal(3.9, thresholds) == "defend"
        assert derive_signal(0.0, thresholds) == "defend"
        assert derive_signal(10.0, thresholds) == "attack"

    def test_custom_thresholds(self):
        thresholds: dict[Signal, float] = {"attack": 8.0, "watch": 5.0}
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
        "signal: {thresholds: {attack: true, watch: 4}}\n",  # bool 不是合法阈值
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


class TestBuildReportSignal:
    def _build(self, tmp_path, final_score):
        """构造单维度结果，控制 final_score"""
        from data.schemas import AnalysisContext, AnalysisResult
        from report.scoring import build_report

        results = [AnalysisResult(
            dimension="financial", status="ok", summary="财务健康",
            score=final_score, metrics={"roe": 0.12},
        )]
        ctx = AnalysisContext(symbol="000001", name="平安银行")
        return build_report("000001", "平安银行", results, {"bulk": "解读"},
                            ctx, no_llm=True, signal_cfg=load_signal_config(Config(config_dir=tmp_path)))

    def test_report_contains_attack_banner(self, tmp_path):
        report = self._build(tmp_path, 8.0)  # final_score=8.0 → 进攻
        assert "信号：进攻" in report
        assert "可考虑建仓/加仓" in report
        assert "60%-80%" in report

    def test_report_contains_defend_banner(self, tmp_path):
        report = self._build(tmp_path, 2.0)  # final_score=2.0 → 防御
        assert "信号：防御" in report
        assert "减仓或回避" in report

    def test_report_without_signal_cfg_has_no_banner(self, tmp_path):
        """signal_cfg=None 时不渲染横幅（兼容现有调用）"""
        from data.schemas import AnalysisContext, AnalysisResult
        from report.scoring import build_report

        results = [AnalysisResult(dimension="financial", status="ok",
                                  summary="财务健康", score=8.0)]
        ctx = AnalysisContext(symbol="000001", name="平安银行")
        report = build_report("000001", "平安银行", results, {"bulk": "解读"},
                              ctx, no_llm=True)
        assert "信号：" not in report
