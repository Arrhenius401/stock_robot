"""ConfigLoader 单元测试"""
from pathlib import Path
import pytest
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from analysis.config_loader import ConfigLoader, ConfigError


CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "src" / "analysis" / "config"


class TestConfigLoader:
    @pytest.fixture
    def loader(self):
        return ConfigLoader(CONFIG_DIR)

    def test_load_base_industry_returns_config(self, loader):
        """未覆写的行业直接返回 base 模板"""
        cfg = loader.load("家用电器")
        assert cfg["meta"]["strategy_key"] == "GeneralScorer"
        assert cfg["financial"]["weight"] == 0.30

    def test_load_bank_returns_merged_config(self, loader):
        """银行配置应为大金融 base + 银行 override 的合并结果"""
        cfg = loader.load("银行")
        assert cfg["meta"]["strategy_key"] == "BankScorer"
        assert cfg["valuation"]["pe_percentile"]["enabled"] is False
        assert cfg["valuation"]["pb_percentile"]["max_score"] == 6
        assert cfg["financial"]["gross_margin_stability"]["enabled"] is False
        assert cfg["valuation"]["weight"] == 0.35

    def test_load_cyclical_returns_reverse_pe(self, loader):
        """周期资源 PE 反转配置"""
        cfg = loader.load("煤炭")
        assert cfg["meta"]["strategy_key"] == "CyclicalScorer"
        assert cfg["valuation"]["pe_percentile"]["percentile_reverse"] is True
        assert cfg["financial"]["gross_margin_stability"]["enabled"] is False

    def test_load_unknown_industry_falls_back(self, loader):
        """未映射的行业降级为高端制造 + GeneralScorer"""
        cfg = loader.load("不存在的行业")
        assert cfg["meta"]["strategy_key"] == "GeneralScorer"

    def test_cache_returns_same_object(self, loader):
        """同行业两次加载返回同一缓存对象"""
        cfg1 = loader.load("银行")
        cfg2 = loader.load("银行")
        assert cfg1 is cfg2

    def test_weights_sum_to_one(self, loader):
        """所有已知行业配置的权重总和 = 1.0"""
        for industry in ["银行", "家用电器", "煤炭", "电子", "医药生物"]:
            cfg = loader.load(industry)
            w = sum(cfg[d]["weight"] for d in ["financial", "valuation", "industry", "technical", "sentiment"])
            assert abs(w - 1.0) < 0.001, f"{industry} 权重总和 {w} != 1.0"

    def test_load_electronics_override(self, loader):
        """电子行业覆写 ROE 阈值"""
        cfg = loader.load("电子")
        assert cfg["meta"]["strategy_key"] == "TechGrowthScorer"
        assert cfg["financial"]["roe"]["max_score"] == 1.5

    def test_config_is_dict(self, loader):
        """加载的配置是可索引的字典"""
        cfg = loader.load("银行")
        assert isinstance(cfg, dict)
        assert "financial" in cfg
        assert "valuation" in cfg
