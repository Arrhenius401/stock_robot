from datetime import date, timedelta

from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexPriceData
from index.analysis.performance import IndexPerformanceAnalyzer
from index.analysis.technical import IndexTechnicalAnalyzer


def context():
    return IndexAnalysisContext(
        target=AnalysisTarget(
            target_type="index", symbol="000300", name="沪深300", index_style="broad"
        ),
        price_data=[
            IndexPriceData(
                symbol="000300",
                trade_date=date(2026, 1, 1) + timedelta(days=i),
                open=100 + i,
                close=101 + i,
                high=102 + i,
                low=99 + i,
                volume=1,
            )
            for i in range(100)
        ],
    )


def test_trend_strength_and_volatility_are_separate():
    m = IndexTechnicalAnalyzer().analyze(context()).metrics
    assert m["adx_14"] == 100
    assert m["atr_20"] > 0
    assert m["direction_medium"]


def test_windows_do_not_annualize_short_sample():
    m = IndexPerformanceAnalyzer().analyze(context()).metrics
    assert [w["period"] for w in m["windows"]] == ["1m", "3m", "12m"]
    assert m["windows"][0]["annualized_volatility_pct"] is None


def test_collection_deadline_terminates_worker():
    import time

    from index.collection_budget import collect_bounded
    started = time.monotonic()
    result = collect_bounded(context().target, budget=0.01)
    assert time.monotonic() - started < 5
    assert result.price_data == []
    assert any("预算" in note for note in result.risk_flags)


def test_holiday_gap_does_not_suppress_volatility():
    ctx = context()
    for p in ctx.price_data[70:]:
        p.trade_date += timedelta(days=7)
    m = IndexPerformanceAnalyzer().analyze(ctx).metrics
    assert m["windows"][-1]["annualized_volatility_pct"] is not None
    assert any("节假日" in note for note in m["windows"][-1]["notes"])


def test_budget_recovers_checkpoint_and_marks_unfinished(mocker, tmp_path):
    import json
    import subprocess
    from pathlib import Path

    from data.collection_state import make_record
    from index.collection_budget import collect_bounded
    saved = context()
    saved.collection_records["index_price"] = make_record("test", "complete")
    process = mocker.Mock()
    process.returncode = -9
    process.poll.return_value = None
    def communicate(payload, timeout):
        if payload:
            output = Path(json.loads(payload)["output"])
            part = output.with_suffix(".part")
            part.write_text(saved.model_dump_json(), encoding="utf-8")
            part.replace(output)
        raise subprocess.TimeoutExpired("test", timeout)
    process.communicate.side_effect = communicate
    mocker.patch("index.collection_budget.subprocess.Popen", return_value=process)
    progress = []
    result = collect_bounded(saved.target, budget=0.1, on_progress=lambda *event: progress.append(event))
    assert result.price_data == saved.price_data
    assert result.collection_records["index_price"].result_state == "complete"
    assert result.collection_records["index_macro"].error_kind == "timeout"
    assert progress
    process.kill.assert_called_once()
    process.wait.assert_called_once()


def test_pb_history_is_independent_of_missing_pe():
    from data.schemas import IndexValuationData
    from index.analysis.valuation import IndexValuationAnalyzer
    ctx = context()
    ctx.valuation_data = IndexValuationData(symbol="000300", date=date(2026, 10, 28), pb=1.36, pb_percentile=25, pb_sample_count=300, valuation_valid=False)
    result = IndexValuationAnalyzer().analyze(ctx)
    assert result.metrics["pb_percentile"] == 25
    assert result.metrics["pe_percentile"] is None


def test_new_official_route_has_split_timeout(mocker, tmp_path):
    from index.valuation_data import StrategyValuationProvider
    response = mocker.Mock()
    response.json.return_value = {"data": []}
    request = mocker.patch("requests.get", return_value=response)
    provider = StrategyValuationProvider(tmp_path / "cache.db", request_timeout=(5, 10))
    assert provider._get_json("https://example.invalid", {"a": 1}) == {"data": []}
    request.assert_called_once_with("https://example.invalid", params={"a": 1}, timeout=(5, 10))
    # 既有策略未传新参数时保持原有12秒超时。
    legacy = StrategyValuationProvider(tmp_path / "legacy.db")
    assert legacy.timeout == 12 and legacy.request_timeout is None

def test_invalid_ohlc_cannot_create_out_of_range_position():
    ctx = context()
    for row in ctx.price_data:
        assert isinstance(row, IndexPriceData)
        row.high = row.close - 1
    result = IndexTechnicalAnalyzer().analyze(ctx)
    assert result.metrics['price_position_pct'] is None
    assert result.metrics['year_high'] is None


def test_wide_index_needs_no_artificial_benchmark():
    result = IndexPerformanceAnalyzer().analyze(context())
    assert result.status == 'ok'
    assert '基准行情不可用' not in result.summary


def test_strategy_unknown_pe_is_not_ttm(mocker, tmp_path):
    from index.valuation_data import StrategyValuationProvider
    provider = StrategyValuationProvider(tmp_path / 'provider.db')
    mocker.patch.object(provider, 'fetch_official_history', return_value=[{'indexCode':'000922','tradeDate':'20261028','close':100,'peg':12}])
    mocker.patch.object(provider, '_factsheet', return_value={})
    mocker.patch.object(provider, '_current_pb', return_value={})
    result=provider.fetch('000922',end=date(2026,10,29))
    assert result is not None
    assert result.pe_ttm is None
    assert result.pe_percentile is None


def test_request_cache_reuses_success_and_preserves_it_on_failure(mocker, tmp_path):
    from data.base import CollectionResult
    from index.request_cache import IndexRequestCache
    cache = IndexRequestCache(tmp_path / "cache.db")
    adapter = mocker.Mock()
    adapter.fetch_result.return_value = CollectionResult(context().price_data)
    first = cache.fetch_cached(adapter, "000300", "index_price", index_style="broad")
    assert cache.fetch_cached(adapter, "000300", "index_price", index_style="broad").rows == first.rows
    assert adapter.fetch_result.call_count == 1
    with cache.cache._get_conn() as connection:
        connection.execute("UPDATE cache SET created_at=created_at-90000 WHERE date_key='status'")
    adapter.fetch_result.return_value = CollectionResult([])
    assert not cache.fetch_cached(adapter, "000300", "index_price", index_style="broad").rows
    assert not cache.fetch_cached(adapter, "000300", "index_price", index_style="broad").rows
    assert adapter.fetch_result.call_count == 2
    assert cache.cache.get_entry("index_price", "000300:broad", "last-success") is not None
    with cache.cache._get_conn() as connection:
        assert connection.execute("SELECT ttl_seconds FROM cache WHERE date_key='status'").fetchone()[0] == 300

def test_official_factsheet_samples_preserve_pb_and_basis():
    from pathlib import Path

    from index.valuation_factsheet import parse_factsheet_text
    # 来源：中证/国证官方单张；2026-10-29采样，2026-09-30月末，两页PDF提取文本。
    # 保留真实列错位布局，不使用理想化PE/PB JSON代替原始解析样本。
    resource = Path(__file__).resolve().parents[2] / 'resources' / 'index'
    for filename, symbol, provider, pe, pb in [
        ('csi-000300-valuation-factsheet-20260930.txt','000300','csi',13.15,1.36),
        ('cni-399006-valuation-factsheet-20260930.txt','399006','cni',31.28,5.2),
    ]:
        result = parse_factsheet_text((resource / filename).read_text(encoding='utf-8'),symbol,provider,date(2026,10,29))
        assert result['pb'] == pb
        assert result['pe_snapshot'] == pe
        assert result['as_of'] == date(2026,9,30)


def test_source_error_remains_diagnostic(mocker, tmp_path):
    from index.valuation_data import StrategyValuationProvider
    provider=StrategyValuationProvider(tmp_path/'source.db')
    mocker.patch.object(provider,'fetch_official_history',return_value=[])
    mocker.patch.object(provider,'_factsheet',side_effect=TimeoutError('oss-ch.csindex.com.cn read timeout=10'))
    result=provider.fetch('000300',end=date(2026,10,29))
    assert result is not None
    assert any('TimeoutError' in note and 'read timeout=10' in note for note in result.valuation_notes)
