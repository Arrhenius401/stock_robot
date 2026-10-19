"""部分财报可缓存，不等于降低评分门槛。"""
from datetime import UTC, date, datetime, timedelta

import pytest

from core.pipeline import Pipeline
from core.registry import Registry
from data.schemas import DataCollectionRecord, FinancialData
from utils.config import Config


@pytest.mark.parametrize("symbol", ["000001", "00700.HK"])
@pytest.mark.parametrize("value", [0, -10, 100])
def test_income_only_facts_are_healthy(symbol, value):
    data = [FinancialData(symbol=symbol, fiscal_quarter=date(2026, 6, 30), net_profit=value)]
    assert Pipeline.__new__(Pipeline)._is_healthy("financial", data)


@pytest.mark.parametrize("value", [None, float("nan"), float("inf")])
def test_empty_or_nonfinite_financials_are_not_healthy(value):
    data = [FinancialData(symbol="00700.HK", fiscal_quarter=date(2026, 6, 30), total_assets=value)]
    assert not Pipeline.__new__(Pipeline)._is_healthy("financial", data)


def test_partial_financial_cache_retains_failure_diagnostic(tmp_path):
    pipeline = Pipeline(Registry(), config=Config(config_dir=tmp_path))
    symbol = "00700.HK"
    data = [FinancialData(symbol=symbol, fiscal_quarter=date(2026, 6, 30), revenue=100)]
    record = DataCollectionRecord(source="东方财富港股财报", collected_at=datetime.now(UTC),
                                  cache_status="live", error_kind="timeout", error_message="资产负债表：数据请求超时")
    pipeline._set_cache(symbol, "financial", data, record)
    restored = pipeline._get_cached(symbol, "financial")
    assert restored and restored[0].revenue == 100
    metadata = pipeline._cache.get_metadata("financial", symbol, "latest")
    assert metadata and "资产负债表" in DataCollectionRecord.model_validate_json(metadata).error_message


def test_wrong_symbol_cannot_be_written_to_cache(tmp_path):
    pipeline = Pipeline(Registry(), config=Config(config_dir=tmp_path))
    data = [FinancialData(symbol="09988.HK", fiscal_quarter=date(2026, 6, 30), revenue=100)]
    pipeline._set_cache("00700.HK", "financial", data)
    assert pipeline._get_cached("00700.HK", "financial") is None


def test_sufficient_backup_history_keeps_primary_failure_in_report_and_cache(tmp_path, monkeypatch):
    from requests.exceptions import ReadTimeout

    from analysis.technical import TechnicalAnalyzer
    from data.akshare import AkShareAdapter
    from data.enrichers import PriceEnricher
    from report.quality import build_data_quality

    calls = []

    def primary(*args):
        calls.append("primary")
        raise ReadTimeout("不能进入报告的原始地址")

    def backup(*args):
        calls.append("backup")
        return [f"{date(2026, 7, 1) + timedelta(days=index)},100,101,102,99,1000"
                for index in range(60)]

    monkeypatch.setattr("data.akshare.tencent_history", primary)
    monkeypatch.setattr("data.akshare.daily_history", backup)
    monkeypatch.setattr("core.pipeline.time.sleep", lambda _: None)
    registry = Registry()
    registry.register_data_source(AkShareAdapter())
    pipeline = Pipeline(registry, Config(config_dir=tmp_path), llm_enabled=False)
    for expected_cache_status in ("live", "cache"):
        ctx = pipeline.collect("00700.HK", "腾讯", market="hk", data_types=["price"])
        ctx = PriceEnricher().enrich(ctx)
        result = TechnicalAnalyzer().analyze(ctx)
        assert result.status == "ok"
        assert ctx.collection_records["price"].cache_status == expected_cache_status
        quality = build_data_quality(ctx, [result])
        dimension = next(item for item in quality["dimensions"] if item["key"] == "technical")
        assert any(field["label"] == "采集限制" and "腾讯港股行情（不复权）：数据请求超时" in field["value"]
                   for field in dimension["fields"])
        assert "不能进入报告的原始地址" not in str(quality)
    assert calls == ["primary", "backup"]
