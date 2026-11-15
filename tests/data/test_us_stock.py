"""美股取数：行情、财务、估值、新闻与 TTM 的离线单元测试。"""
from datetime import date, timedelta
from typing import Any

import pandas as pd
import pytest

from data.schemas import FinancialData
from data.us_stock import us_financials, us_history, us_news, us_ttm, us_valuation


def _price_frame(start: date, days: int = 60) -> pd.DataFrame:
    rows = []
    for index in range(days):
        close = 10 + index * 0.5
        rows.append({
            "date": start + timedelta(days=index),
            "open": close - 0.2, "high": close + 0.4, "low": close - 0.6,
            "close": close, "volume": 1000 + index,
        })
    return pd.DataFrame(rows)


def _statement_frames() -> dict[str, Any]:
    periods = ("2025-09-26", "2025-12-26", "2026-03-27", "2026-06-26", "2026-09-25")
    income = {}
    cash = {}
    for index, period in enumerate(periods):
        revenue = 100 + index * 10
        income[period] = {
            "004001999": revenue,
            "004015999": 10 + index,
            "004005999": revenue * 0.7,
            "004017003": 1.0 + index,
        }
        cash[period] = {"003999": 20 + index}
    # 资产负债表缺失最新期，验证回退到最近可得日期且不混算 ROE。
    balance = {
        periods[0]: {"004005999": 1000, "004013999": 500, "004017999": 500},
        periods[1]: {"004005999": 1010, "004013999": 505, "004017999": 505},
        periods[2]: {"004005999": 1020, "004013999": 510, "004017999": 510},
        periods[3]: {"004005999": 1030, "004013999": 515, "004017999": 515},
    }
    return {"综合损益表": income, "资产负债表": balance, "现金流量表": cash}


def test_us_history_parses_and_calculates_change(mocker):
    start, end = date(2026, 1, 1), date(2026, 3, 1)
    mocker.patch("akshare.stock_us_daily", return_value=_price_frame(start))
    rows = us_history("TEST", start=start, end=end)
    assert len(rows) == 60
    assert rows[0].symbol == "TEST"
    assert rows[-1].change_pct == pytest.approx(
        round((rows[-1].close / rows[-2].close - 1) * 100, 2))


def test_us_financials_normalizes_period_and_derives_ttm(mocker):
    frames = _statement_frames()
    mocker.patch("data.us_stock._statement", side_effect=lambda _ticker, name: frames[name])
    mocker.patch("data.us_stock._annual_roe", return_value=(1.5, date(2026, 9, 26)))
    rows = us_financials("MSFT")
    assert len(rows) == 5
    latest = rows[-1]
    assert latest.fiscal_quarter == date(2026, 9, 30)
    assert latest.period_start == date(2026, 7, 1)
    assert latest.period_basis == "single"
    assert latest.report_currency == "USD"
    assert latest.amount_currency_verified is True
    assert latest.gross_margin == pytest.approx(0.7)
    # 最新期资产负债表缺失，回退到最近可得日期并在字段来源标注。
    assert latest.total_assets == 1030
    assert "2026-06-26" in latest.field_sources["total_assets"]
    assert "资产负债表最近可得 2026-06-26" in latest.financial_selection_note
    # 有完整资产负债表的那一期按四季净利/权益派生 ROE（小数，不是百分数）。
    complete = rows[3]
    assert complete.field_sources.get("roe") == "按最近四个财季净利润/期末归母权益派生"
    assert complete.roe == pytest.approx((10 + 11 + 12 + 13) / 515)
    # 最新期无同期资产负债表，回退最新年报 ROE_AVG，并标注口径。
    assert latest.roe == pytest.approx(1.5)
    assert latest.field_sources["roe"] == "最新年报 ROE_AVG（2026-09-26）"
    assert "最近四个财季合计为 TTM" in latest.financial_selection_note


def test_us_ttm_sums_last_four_quarters_only():
    rows = [
        FinancialData(symbol="MSFT", fiscal_quarter=date(2026, month, 30),
                      revenue=10, net_profit=1, operating_cash_flow=2,
                      total_equity=50, common_equity=50)
        for month in (3, 6, 9, 12)
    ]
    result = us_ttm(rows)
    assert result["revenue"] == 40
    assert result["net_profit"] == 4
    assert result["operating_cash_flow"] == 8
    assert result["equity"] == 50


class _BaiduResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _baidu_payload(ticker: str, metric: str, value: float) -> dict:
    return {"Result": [{"DisplayData": {"resultData": {"tplData": {"result": {
        "code": ticker,
        "chartInfo": [{"header": [metric], "body": [
            ["2026-11-12", str(value - 0.1)],
            ["2026-11-13", str(value)],
        ]}],
    }}}}}]}


def test_us_valuation_parses_snapshot_and_history(mocker):
    def fake_get(_url, params, timeout):
        metric = params["query"]
        value = 27.28 if metric == "市盈率(TTM)" else 8.27
        return _BaiduResponse(_baidu_payload("MSFT", metric, value))

    mocker.patch("data.us_stock.requests.get", side_effect=fake_get)
    mocker.patch("data.us_stock.us_quote", return_value={
        "market_cap": 3890434802425, "quoted_at": "2026-11-14 09:16:08",
    })
    item = us_valuation("MSFT")[0]
    assert item.pe_ttm == pytest.approx(27.28)
    assert item.pb == pytest.approx(8.27)
    assert item.pe_as_of == date(2026, 11, 13)
    assert item.pb_as_of == date(2026, 11, 13)
    assert item.market_cap == pytest.approx(3890434802425)
    assert item.market_cap_as_of == date(2026, 11, 14)
    assert len(item.pe_history) == 2
    assert item.history_source == "百度美股估值（近一年）"
    assert any("PS(TTM)" in note for note in item.valuation_notes)
    assert item.cache_degraded is False


def test_us_valuation_marks_single_metric_failure(mocker):
    def fake_get(_url, params, timeout):
        if params["query"] == "市盈率(TTM)":
            raise ValueError("pe unavailable")
        return _BaiduResponse(_baidu_payload("MSFT", "市净率", 8.27))

    mocker.patch("data.us_stock.requests.get", side_effect=fake_get)
    mocker.patch("data.us_stock.us_quote", return_value={"market_cap": None})
    item = us_valuation("MSFT")[0]
    assert item.pe_ttm is None
    assert item.pb == pytest.approx(8.27)
    assert item.cache_degraded is True
    assert "pe_history" in item.failed_components


def test_us_news_filters_relevance_and_deduplicates(mocker):
    rows = [
        {"date": "2026-11-13 10:00:00", "title": "微软发布新服务", "content": "微软表示服务恢复",
         "mediaName": "财联社", "url": "https://example.com/1"},
        {"date": "2026-11-13 11:00:00", "title": "市场综述", "content": "正文提到微软但标题无关",
         "mediaName": "示例", "url": "https://example.com/2"},
        {"date": "2026-11-13 10:00:00", "title": "微软发布新服务", "content": "重复链接",
         "mediaName": "财联社", "url": "https://example.com/1"},
    ]
    mocker.patch("data.us_stock.search_news", return_value=rows)
    result = us_news("MSFT", "微软")[0]
    assert result.headlines == ["微软发布新服务"]
    assert result._raw_sentiment is not None
    assert len(result._raw_sentiment.items) == 1
    assert result._raw_sentiment.items[0].media == "财联社"
