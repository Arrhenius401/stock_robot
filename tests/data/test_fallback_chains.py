"""数据源回退链测试：总股本三级链"""
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


def _make_info_df(item: str, value: str) -> pd.DataFrame:
    return pd.DataFrame({"item": [item], "value": [value]})


class TestGetTotalShares:
    def test_eastmoney_first(self, mocker):
        """在线源优先：东财总股本可用时直接返回"""
        from data.akshare import get_total_shares
        mocker.patch("data.akshare._ak_individual_info_em",
                     return_value=_make_info_df("总股本", "194.05亿"))
        assert get_total_shares("000001") == 19405000000.0

    def test_falls_to_tencent(self, mocker):
        """东财失败 → 腾讯流通股本"""
        from data.akshare import get_total_shares
        mocker.patch("data.akshare._ak_individual_info_em",
                     side_effect=ConnectionError("mock"))
        daily_df = pd.DataFrame({
            "date": ["2026-08-22", "2026-08-21"],
            "outstanding_share": [1.9405e10, 1.9405e10],
        })
        mocker.patch("data.akshare._ak_daily", return_value=daily_df)
        assert get_total_shares("000001") == 1.9405e10

    def test_falls_to_financial_reverse(self, mocker):
        """东财+腾讯都失败 → 财报反推 net_profit/basic_eps"""
        from data.akshare import get_total_shares
        from data.schemas import FinancialData
        mocker.patch("data.akshare._ak_individual_info_em",
                     side_effect=ConnectionError("mock"))
        mocker.patch("data.akshare._ak_daily",
                     side_effect=ConnectionError("mock"))
        financials = [FinancialData(
            symbol="000001",
            fiscal_quarter=datetime.now().astimezone().date(),
            net_profit=25695999999.999996, basic_eps=1.324,
        )]
        assert get_total_shares("000001", financials) == pytest.approx(25695999999.999996 / 1.324)

    def test_reject_single_quarter_eps(self, mocker):
        """财报反推取最新期累计口径：net_profit 与 basic_eps 同期间相除"""
        from data.akshare import get_total_shares
        from data.schemas import FinancialData
        mocker.patch("data.akshare._ak_individual_info_em",
                     side_effect=ConnectionError("mock"))
        mocker.patch("data.akshare._ak_daily",
                     side_effect=ConnectionError("mock"))
        financials = [FinancialData(
            symbol="000001",
            fiscal_quarter=datetime.now().astimezone().date(),
            net_profit=13000000000, basic_eps=0.67,
        )]
        result = get_total_shares("000001", financials)
        assert result == pytest.approx(13000000000 / 0.67)

    def test_basic_eps_invalid_returns_none(self, mocker):
        """basic_eps 非正或缺失时财报反推不可用"""
        from data.akshare import get_total_shares
        from data.schemas import FinancialData
        mocker.patch("data.akshare._ak_individual_info_em",
                     side_effect=ConnectionError("mock"))
        mocker.patch("data.akshare._ak_daily",
                     side_effect=ConnectionError("mock"))
        financials = [FinancialData(
            symbol="000001",
            fiscal_quarter=datetime.now().astimezone().date(),
            net_profit=13000000000, basic_eps=-1,
        )]
        assert get_total_shares("000001", financials) is None

    def test_all_sources_fail_returns_none(self, mocker):
        """全链失败返回 None"""
        from data.akshare import get_total_shares
        mocker.patch("data.akshare._ak_individual_info_em",
                     side_effect=ConnectionError("mock"))
        mocker.patch("data.akshare._ak_daily",
                     side_effect=ConnectionError("mock"))
        assert get_total_shares("000001") is None


class TestGetIndustryName:
    def test_eastmoney_first(self, mocker):
        """在线源优先：东财行业可用时返回"""
        from data.akshare import _get_industry_name
        info_df = pd.DataFrame({"item": ["行业", "总股本"], "value": ["银行", "194.05亿"]})
        mocker.patch("data.akshare._ak_individual_info_em", return_value=info_df)
        assert _get_industry_name("000001") == "银行"

    def _patch_mapping(self, mocker, sw_level1: str):
        """patch 本地映射表 lookup 返回值"""
        fake = type("Fake", (), {
            "lookup": lambda self, s: type("R", (), {"sw_level1": sw_level1})()})()
        mocker.patch("data.industry_classifier.IndustryClassifier", return_value=fake)

    def test_falls_to_local_mapping(self, mocker):
        """东财失败 → 本地映射表（非占位值时生效）"""
        from data.akshare import _get_industry_name
        mocker.patch("data.akshare._ak_individual_info_em",
                     side_effect=ConnectionError("mock"))
        self._patch_mapping(mocker, "银行")
        assert _get_industry_name("000001") == "银行"

    def test_placeholder_mapping_returns_empty(self, mocker):
        """本地表占位值'综合'不得作为行业名使用"""
        from data.akshare import _get_industry_name
        mocker.patch("data.akshare._ak_individual_info_em",
                     side_effect=ConnectionError("mock"))
        self._patch_mapping(mocker, "综合")
        assert _get_industry_name("000001") == ""

    def test_all_fail_returns_empty(self, mocker):
        from data.akshare import _get_industry_name
        mocker.patch("data.akshare._ak_individual_info_em",
                     side_effect=ConnectionError("mock"))
        mocker.patch("data.industry_classifier.IndustryClassifier",
                     side_effect=FileNotFoundError("mock"))
        assert _get_industry_name("000001") == ""
