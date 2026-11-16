"""财务、分红逐公司通道的并发上限。"""
from __future__ import annotations

import threading
import time
from datetime import date

from radar.stocks.update_runner import StockUpdateRunner
from tests.radar.test_stock_financial_source import environment

DAY = date(2026, 10, 15)
SYMBOLS = ('600900', '600901', '600902', '600903', '600904', '600905')


class Tracker:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()

    def enter(self) -> None:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)

    def leave(self) -> None:
        with self.lock:
            self.active -= 1


def snapshot(kind: str) -> dict:
    return {'kind': kind, 'pool_ids': ['dividend_value'], 'scope_complete': True,
            'scope_as_of': DAY.isoformat(), 'scope_sha256': 'b' * 64, 'source_run_id': 'scope',
            'companies': [{'symbol': symbol, 'name': symbol} for symbol in SYMBOLS]}


def test_financial_uses_bounded_concurrency(tmp_path, monkeypatch):
    import types

    monkeypatch.setattr('radar.stocks.update_runner.verify_financial_payload',
                        lambda *args, **kwargs: {'financial_numeric_status': 'passed'})
    monkeypatch.setattr('radar.stocks.update_runner.collection_result',
                        lambda value, kind: types.SimpleNamespace(
                            value=value,
                            record=types.SimpleNamespace(result_state='complete',
                                                        model_dump=lambda mode: {'result_state': 'complete'})))
    _, _, tasks, _ = environment(tmp_path)
    tasks.enqueue(kind='financial_review', pool_ids=['dividend_value'], target_date=DAY,
                  rule_version='common-approved-2026-10-29-v3', snapshot=snapshot('financial_review'))
    tracker = Tracker()

    def collect(symbol, cache, *, as_of):
        tracker.enter()
        time.sleep(0.05)
        tracker.leave()
        return {'symbol': symbol, 'as_of': as_of.isoformat(), 'source_stopped': False}

    runner = StockUpdateRunner(tasks, tmp_path / 'cache', tmp_path / 'stock-updates/artifacts',
                               financial_collector=collect)
    finished = runner.execute_one('test')
    assert finished is not None and finished['status'] == 'completed'
    assert tracker.peak == 4


def test_dividend_uses_bounded_concurrency(tmp_path, monkeypatch):
    monkeypatch.setattr('radar.stocks.update_runner.verify_collection', lambda *args, **kwargs: None)
    _, _, tasks, _ = environment(tmp_path)
    tasks.enqueue(kind='dividend_review', pool_ids=['dividend_value'], target_date=DAY,
                  rule_version='stock-dividend-structured-v2', snapshot=snapshot('dividend_review'))
    tracker = Tracker()

    def collect(symbol, as_of, cache, *, blocked_sources):
        tracker.enter()
        time.sleep(0.05)
        tracker.leave()
        return {'symbol': symbol, 'as_of': as_of.isoformat()}

    runner = StockUpdateRunner(tasks, tmp_path / 'cache', tmp_path / 'stock-updates/artifacts',
                               dividend_collector=collect)
    finished = runner.execute_one('test')
    assert finished is not None and finished['status'] == 'completed'
    assert tracker.peak == 4
