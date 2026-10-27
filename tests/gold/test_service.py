"""黄金服务边界：保持事实不受 AI 影响，归档与正文绑定。"""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from gold.models import GoldBar, GoldCollection, GoldRules, GoldSeries
from gold.service import GoldService, is_gold_request, load_gold_snapshot, load_rules


@pytest.mark.parametrize("text", ["黄金", "GOLD", "Au99.99", "XAUUSD", "请分析一下黄金", "国际黄金值得买吗？",
                                  "黄金价格", "今天金价", "分析国际金价"])
def test_gold_aliases(text):
    assert is_gold_request(text)


@pytest.mark.parametrize("text", ["山东黄金", "分析山东黄金600547", "黄金ETF", "黄金股票有哪些", "黄金首饰价格", ""])
def test_stock_and_product_names_do_not_route_to_gold(text):
    assert not is_gold_request(text)


def test_service_preserves_facts_on_ai_failure_and_archives(tmp_path, mocker):
    now = datetime(2026, 10, 26, 9, tzinfo=UTC)
    collector = mocker.Mock()
    collector.collect.return_value = GoldCollection(generated_at=now)
    config = SimpleNamespace(config_dir=tmp_path, get=lambda key, default=None: default)
    llm = mocker.Mock()
    llm.generate.side_effect = RuntimeError("SDK failure")
    analysis = {"long_term": {"direction": "无法判断"}, "markets": {}, "fx_decomposition": []}
    service = GoldService(config=config, collector=collector, llm=llm,
                          output_dir=tmp_path / "reports", analyzer=lambda *_: analysis,
                          renderer=lambda p: "# 黄金综合研报\n\n" + p["long_term"]["direction"])
    result = service.run(now=now, refresh_cache=True)
    assert result["long_term"]["direction"] == "无法判断"
    assert result["commentary_status"] == "failed"
    assert llm.generate.call_count == 1
    collector.collect.assert_called_once_with(now=now, refresh=True)
    path = tmp_path / "reports" / result["report_path"]
    assert path.exists()
    snapshot = load_gold_snapshot(path, path.read_text(encoding="utf-8"))
    assert snapshot is not None
    assert snapshot["rules_snapshot"] == GoldRules().model_dump()
    path.write_text("# 改过的报告", encoding="utf-8")
    assert load_gold_snapshot(path, path.read_text(encoding="utf-8")) is None


def test_disabled_ai_is_not_called_and_unknown_rules_rejected(tmp_path, mocker):
    collector = mocker.Mock()
    collector.collect.return_value = GoldCollection(generated_at=datetime.now().astimezone())
    config = SimpleNamespace(config_dir=tmp_path, get=lambda key, default=None: False if key == "llm.enabled" else default)
    llm = mocker.Mock()
    service = GoldService(config=config, collector=collector, llm=llm,
                          analyzer=lambda *_: {"long_term": {}, "markets": {}, "fx_decomposition": []},
                          renderer=lambda _: "# 黄金报告")
    report = service.run(save=False)
    assert report["commentary_status"] == "disabled"
    llm.generate.assert_not_called()
    with pytest.raises(ValueError):
        load_rules(SimpleNamespace(get=lambda *_: {"unknown_rule": 1}))


@pytest.mark.parametrize("settings", [{"rate_threshold_bp": float("inf")},
    {"source_timeout_seconds": 31}, {"total_timeout_seconds": 61},
    {"significant_low_percentile": 90}, {"reserve_marginal_months": 6}, {"annual_days": 1}])
def test_rules_reject_invalid_budgets_and_thresholds(settings):
    with pytest.raises(ValueError):
        GoldRules(**settings)


def test_alarm_state_persists_without_reading_future_state(tmp_path, mocker):
    now = datetime(2026, 10, 26, 9, tzinfo=UTC)
    collector = mocker.Mock()
    def collect(now=None, refresh=False):
        assert now is not None
        return GoldCollection(generated_at=now, series={'domestic': GoldSeries(
            key='domestic', name='国内黄金', source='测试', source_url='https://example.com', unit='元/克',
            fetched_at=now, bars=[GoldBar(trade_date=now.date(), open=100, close=100, low=99, high=101)])})
    collector.collect.side_effect = collect
    observed = []
    def analyze(collection, _):
        observed.append(collection.series['domestic'].metadata.get('overheated_active'))
        return {'long_term': {}, 'markets': {'domestic': {'as_of': now.date().isoformat(),
            'short_term': {'direction': '偏多'}, 'risks': [{'key': 'overheated', 'active': False}]}}}
    config = SimpleNamespace(config_dir=tmp_path, get=lambda key, default=None: default)
    service = GoldService(config=config, collector=collector, analyzer=analyze, renderer=lambda _: '# 黄金')
    service.run(now=now, save=False, no_llm=True)
    service.run(now=now + timedelta(minutes=1), save=False, no_llm=True)
    service.run(now=now - timedelta(minutes=1), save=False, no_llm=True)
    service.run(now=now + timedelta(minutes=2), save=False, no_llm=True)
    assert observed == [None, False, None, False]
    assert len(list((tmp_path / 'gold').glob('alerts-*.json'))) == 1


def test_parallel_alarm_evaluation_uses_latest_committed_state(tmp_path, mocker):
    now = datetime(2026, 10, 26, 9, tzinfo=UTC)
    entered = threading.Event()
    second_collected = threading.Event()
    release = threading.Event()
    seen = []
    def collect(now=None, refresh=False):
        assert now is not None
        if now.minute == 2:
            second_collected.set()
        return GoldCollection(generated_at=now, series={'domestic': GoldSeries(
            key='domestic', name='国内黄金', source='测试', source_url='https://example.com', unit='元/克',
            fetched_at=now, bars=[GoldBar(trade_date=now.date(), open=100, close=100, low=99, high=101)])})
    def analyze(collection, _):
        stamp = collection.generated_at.minute
        prior = collection.series['domestic'].metadata.get('overheated_active')
        seen.append((stamp, prior))
        if stamp == 1:
            entered.set()
            assert release.wait(2)
        active = stamp == 1 or prior is True
        return {'long_term': {}, 'markets': {'domestic': {'as_of': now.date().isoformat(),
            'short_term': {'direction': '偏多'}, 'risks': [{'key': 'overheated', 'active': active}]}}}
    config = SimpleNamespace(config_dir=tmp_path, get=lambda key, default=None: default)
    collector = mocker.Mock()
    collector.collect.side_effect = collect
    service = GoldService(config=config, collector=collector, analyzer=analyze, renderer=lambda _: '# 黄金')
    service.run(now=now, save=False, no_llm=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.run, now=now + timedelta(minutes=1), save=False, no_llm=True)
        assert entered.wait(2)
        second = pool.submit(service.run, now=now + timedelta(minutes=2), save=False, no_llm=True)
        assert second_collected.wait(2)
        release.set()
        first.result()
        report = second.result()
    assert seen[-1] == (2, True)
    assert report['markets']['domestic']['risks'][0]['active'] is True
