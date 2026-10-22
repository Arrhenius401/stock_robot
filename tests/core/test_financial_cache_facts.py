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


def test_a_financial_provenance_and_position_evidence_roundtrip(tmp_path):
    pipeline = Pipeline(Registry(), config=Config(config_dir=tmp_path), llm_enabled=False)
    row = FinancialData(
        symbol="600519", fiscal_quarter=date(2026, 6, 30), revenue=100, net_profit=10,
        financial_source="主源", field_sources={"revenue": "主源", "net_profit": "备用"},
        field_positions={"net_profit": {"revision": "2026中报原始版", "currency": "CNY"}},
        financial_selection_status="partial", financial_selection_note="等效补位后仍有缺项",
    )
    pipeline._set_cache("600519", "financial", [row])
    restored = pipeline._get_cached("600519", "financial")
    assert restored
    assert restored[0].model_dump() == row.model_dump()


def test_partial_selection_not_reclassified_complete_by_number_of_periods():
    from data.enrichers import FinancialEnricher
    from data.schemas import AnalysisContext, SufficiencyLevel

    rows = [FinancialData(
        symbol="600519", fiscal_quarter=period, revenue=100, net_profit=10,
        financial_selection_status="partial", financial_selection_note="最新资产数据缺失",
    ) for period in (date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30))]
    ctx = AnalysisContext(symbol="600519", name="茅台", market="a-shares", financial_data=rows)
    FinancialEnricher().enrich(ctx)
    assert ctx.sufficiency.financial.level == SufficiencyLevel.PARTIAL
    assert "缺失" in ctx.sufficiency.financial.reason


def test_legacy_cache_without_selection_metadata_retains_original_behavior():
    from data.enrichers import FinancialEnricher
    from data.schemas import AnalysisContext, SufficiencyLevel

    rows = [FinancialData(symbol="600519", fiscal_quarter=period, revenue=100, net_profit=10)
            for period in (date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30))]
    ctx = AnalysisContext(symbol="600519", name="茅台", market="a-shares", financial_data=rows)
    FinancialEnricher().enrich(ctx)
    assert ctx.sufficiency.financial.level == SufficiencyLevel.SUFFICIENT


def test_quality_keeps_latest_field_sources_and_partial_note():
    from analysis.financial import FinancialAnalyzer
    from data.enrichers import FinancialEnricher
    from data.schemas import AnalysisContext
    from report.quality import build_data_quality

    row = FinancialData(
        symbol="600519", fiscal_quarter=date(2026, 6, 30), revenue=100, net_profit=10,
        financial_source="主源", field_sources={"revenue": "主源", "net_profit": "备用"},
        financial_selection_status="partial", financial_selection_note="等效补位后仍有缺项",
    )
    ctx = AnalysisContext(symbol="600519", name="茅台", financial_data=[row])
    FinancialEnricher().enrich(ctx)
    result = FinancialAnalyzer().analyze(ctx)
    quality = build_data_quality(ctx, [result])
    fields = quality["dimensions"][0]["fields"]
    assert any(f["label"] == "最新报告字段来源" and "备用" in f["value"] for f in fields)
    assert any(f["label"] == "财务包说明" and "仍有缺项" in f["value"] for f in fields)
    assert not quality["signal_allowed"]


def test_a_financial_backup_full_pipeline_report_and_cache(tmp_path, monkeypatch):
    """只替换外部供应商响应，实际适配器/分析/SQLite/报告路径均真实执行。"""
    import json
    from pathlib import Path
    from types import SimpleNamespace

    from analysis.financial import FinancialAnalyzer
    from data.akshare import AkShareAdapter
    from report.quality import build_data_quality
    from report.scoring import build_report

    raw = json.loads((Path(__file__).parents[1] / "resources/data/a-financial-sina-600519.json").read_text(encoding="utf-8"))
    payloads = {item["label"]: item["payload"] for item in raw["responses"]}
    calls = []

    def primary(*_args, **_kwargs):
        raise TimeoutError("不应写进报告的供应商响应")

    def get(url, *, params, timeout):
        assert "CompanyFinanceService.getFinanceReport2022" in url
        assert timeout == 10
        calls.append(params["source"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payloads[params["source"]])

    monkeypatch.setattr(AkShareAdapter, "_fetch_ths_financial", primary)
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr("core.pipeline.time.sleep", lambda _: None)
    registry = Registry()
    registry.register_data_source(AkShareAdapter())
    registry.register_analysis_module(FinancialAnalyzer())
    pipeline = Pipeline(registry, Config(config_dir=tmp_path), llm_enabled=False)
    for cache_status in ("live", "cache"):
        results, commentary, ctx = pipeline.run("600519", "贵州茅台", dimension="financial")
        assert ctx.collection_records["financial"].cache_status == cache_status
        assert ctx.financial_data
        assert ctx.financial_data[-1].financial_source == "新浪财报"
        assert ctx.financial_data[-1].field_sources["net_profit"] == "新浪财报"
        assert ctx.financial_data[-1].cash_flow_basis == "reported"
        assert commentary == {}
        quality = build_data_quality(ctx, results)
        fields = quality["dimensions"][0]["fields"]
        assert any(field["label"] == "采集限制" and "超时" in field["value"] for field in fields)
        markdown = build_report("600519", "贵州茅台", results, commentary, ctx, no_llm=True)
        assert "新浪财报" in markdown and "备用来源" in markdown
        assert "不应写进报告" not in markdown
    assert calls == ["lrb", "fzb", "llb", "gjzb"]


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
