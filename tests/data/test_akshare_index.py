"""AkShare 指数数据采集测试"""
from datetime import date

import pandas as pd
import pytest

from data.akshare import AkShareAdapter
from data.schemas import IndexPriceData


def test_index_valuation_keeps_unknown_daily_pe_out_of_ttm(mocker):
    from data.schemas import IndexValuationData
    provider = mocker.patch("index.valuation_data.StrategyValuationProvider").return_value
    provider.fetch.return_value = IndexValuationData(symbol="000300", date=date(2026, 10, 28), pe_ttm=12.76, pe_as_of=date(2026, 10, 28), pe_percentile=57.1)
    result = AkShareAdapter().fetch("000300", data_type="index_valuation")
    assert result[0].pe_ttm is None
    assert result[0].pe_snapshot == 12.76
    assert result[0].pe_percentile is None
    assert result[0].valuation_valid is False
    provider.fetch.assert_called_once_with("000300", provider="csi")


def test_index_valuation_routes_chinext_to_cni_snapshot(mocker):
    from data.schemas import IndexValuationData
    provider = mocker.patch("index.valuation_data.StrategyValuationProvider").return_value
    # 2026-10-29探针：国证399006官方2026年9月单张PE31.28、PB5.20，未声明TTM。
    provider.fetch.return_value = IndexValuationData(symbol="399006", date=date(2026, 9, 30), pe_snapshot=31.28, pb=5.2, valuation_valid=False)
    result = AkShareAdapter().fetch("399006", data_type="index_valuation")
    assert result[0].pe_snapshot == 31.28
    assert result[0].pe_ttm is None
    assert result[0].pb == 5.2
    provider.fetch.assert_called_once_with("399006", provider="cni")


class TestAkShareIndexAdapter:
    def test_supports_index_price(self):
        adapter = AkShareAdapter()
        assert adapter.supports("a-shares", "index_price") is True

    def test_supports_index_valuation(self):
        adapter = AkShareAdapter()
        assert adapter.supports("a-shares", "index_valuation") is True

    def test_fetch_index_price_broad(self):
        adapter = AkShareAdapter()
        result = adapter.fetch("000300", data_type="index_price",
                               index_style="broad")
        assert isinstance(result, list)
        if len(result) > 0:
            item = result[0]
            assert isinstance(item, IndexPriceData)
            assert item.symbol == "000300"
            assert item.close > 0

    def test_fetch_index_price_returns_empty_on_error(self):
        adapter = AkShareAdapter()
        result = adapter.fetch("INVALID_INDEX", data_type="index_price")
        assert result == []


def test_fetch_index_price_computes_change_pct_without_column(mocker):
    """指数源无涨跌幅列（安装版 akshare 现状）：按前收盘计算，首行为 None"""

    def _mock_em(symbol):
        return pd.DataFrame([
            {"date": "2026-07-01", "open": "3000.0", "high": "3050.0", "low": "2990.0",
             "close": "3000.0", "volume": 100000, "amount": 5.0e8},
            {"date": "2026-07-02", "open": "3010.0", "high": "3060.0", "low": "3000.0",
             "close": "3030.0", "volume": 110000, "amount": 5.5e8},
        ])

    mocker.patch("akshare.stock_zh_index_daily_em", side_effect=_mock_em)
    adapter = AkShareAdapter()
    results = adapter.fetch("000300", data_type="index_price", index_style="broad")
    assert len(results) == 2
    assert results[0].change_pct is None  # 首行无前收盘
    assert results[1].change_pct == pytest.approx(1.0)  # (3030-3000)/3000*100


def test_fetch_index_price_uses_pct_column(mocker):
    """指数源提供涨跌幅列时取列值（列优先于前收盘计算）"""

    def _mock_em(symbol):
        return pd.DataFrame([
            {"date": "2026-07-01", "open": "3000.0", "high": "3050.0", "low": "2990.0",
             "close": "3000.0", "volume": 100000, "amount": 5.0e8, "涨跌幅": "1.20"},
            {"date": "2026-07-02", "open": "3010.0", "high": "3060.0", "low": "3000.0",
             "close": "3030.0", "volume": 110000, "amount": 5.5e8, "涨跌幅": "1.00"},
        ])

    mocker.patch("akshare.stock_zh_index_daily_em", side_effect=_mock_em)
    adapter = AkShareAdapter()
    results = adapter.fetch("000300", data_type="index_price", index_style="broad")
    assert len(results) == 2
    assert results[0].change_pct == pytest.approx(1.2)
    assert results[1].change_pct == pytest.approx(1.0)


def test_fetch_index_price_sector_accepts_chinese_columns(mocker):
    """申万行业指数接口返回中文列名时，应正常解析而不是整批跳过"""

    def _mock_sw(symbol):
        return pd.DataFrame([
            {"日期": "2026-07-01", "开盘": "3000.0", "最高": "3050.0",
             "最低": "2990.0", "收盘": "3000.0", "成交量": 100000,
             "成交额": 5.0e8, "涨跌幅": "1.20"},
            {"日期": "2026-07-02", "开盘": "3010.0", "最高": "3060.0",
             "最低": "3000.0", "收盘": "3030.0", "成交量": 110000,
             "成交额": 5.5e8, "涨跌幅": "1.00"},
        ])

    sw = mocker.patch("akshare.index_hist_sw", side_effect=_mock_sw)
    adapter = AkShareAdapter()
    results = adapter.fetch("801010", data_type="index_price", index_style="sector")

    sw.assert_called_once_with(symbol="801010")
    assert len(results) == 2
    assert results[0].symbol == "801010"
    assert results[0].close == pytest.approx(3000.0)
    assert results[0].turnover == pytest.approx(5.0)
    assert results[1].change_pct == pytest.approx(1.0)


def test_fetch_overseas_index_falls_back_to_sina_hk(mocker):
    """东财全球指数接口失败时回退新浪港股日线源（HSI 走 stock_hk_index_daily_sina）"""

    def _mock_sina(symbol):
        return pd.DataFrame([
            {"date": "2026-07-01", "open": 25000.0, "high": 25200.0,
             "low": 24900.0, "close": 25100.0, "volume": 1000},
            {"date": "2026-07-02", "open": 25100.0, "high": 25300.0,
             "low": 25000.0, "close": 25200.0, "volume": 1200},
        ])

    mocker.patch("akshare.index_global_hist_em", side_effect=ConnectionError("东财失效"))
    sina = mocker.patch("akshare.stock_hk_index_daily_sina", side_effect=_mock_sina)
    adapter = AkShareAdapter()
    results = adapter.fetch("HSI", data_type="index_price", index_style="overseas")
    sina.assert_called_once_with(symbol="HSI")
    assert len(results) == 2
    assert results[0].close == pytest.approx(25100.0)
    assert results[1].change_pct == pytest.approx(0.4)


def test_fetch_overseas_index_falls_back_to_sina_us(mocker):
    """东财主源返回空时回退新浪美股日线源（SPX 映射 .INX）"""

    def _mock_us(symbol):
        return pd.DataFrame([
            {"date": "2026-07-01", "open": 5000.0, "high": 5050.0,
             "low": 4980.0, "close": 5020.0, "volume": 1000},
        ])

    mocker.patch("akshare.index_global_hist_em", return_value=pd.DataFrame())
    us = mocker.patch("akshare.index_us_stock_sina", side_effect=_mock_us)
    adapter = AkShareAdapter()
    results = adapter.fetch("SPX", data_type="index_price", index_style="overseas")
    us.assert_called_once_with(symbol=".INX")
    assert len(results) == 1
    assert results[0].close == pytest.approx(5020.0)


def test_fetch_overseas_index_unknown_symbol_returns_empty(mocker):
    """无新浪映射的海外符号且东财失败时返回空列表而非崩溃"""
    mocker.patch("akshare.index_global_hist_em", side_effect=ConnectionError("东财失效"))
    adapter = AkShareAdapter()
    results = adapter.fetch("UNKNOWN_INDEX", data_type="index_price", index_style="overseas")
    assert results == []


def test_index_history_result_reports_source_and_state(monkeypatch):
    frame = pd.DataFrame({"date": ["2024-01-02"], "close": [3500.0]})
    monkeypatch.setattr("data.akshare._ak_csindex", lambda **_: frame)
    from data.akshare import fetch_index_history_result

    result = fetch_index_history_result("000300", "20240101", "20240131")
    assert result.value is frame
    assert result.record.source == "中证指数官网"
    assert result.record.result_state == "complete"
    assert result.record.collected_at is not None


def test_index_history_result_empty_and_safe_failure(monkeypatch):
    from data.akshare import fetch_index_history_result

    monkeypatch.setattr("data.akshare._ak_csindex", lambda **_: None)
    empty = fetch_index_history_result("000300", "20240101", "20240131")
    assert empty.value is None
    assert empty.record.result_state == "empty"

    def fail(**kwargs):
        raise TimeoutError("敏感的上游细节")

    monkeypatch.setattr("data.akshare._ak_csindex", fail)
    failed = fetch_index_history_result("000300", "20240101", "20240131")
    assert failed.value is None
    assert failed.record.result_state == "failed"
    assert failed.record.error_kind == "timeout"
    assert "敏感的上游细节" not in failed.record.error_message

@pytest.mark.parametrize('timestamp, source', [(float('nan'), float('nan')), ('not-a-date', '媒体'), (pd.NaT, pd.NA)])
def test_index_news_invalid_metadata_stays_missing(mocker, timestamp, source):
    from data.schemas import AnalysisTarget, IndexAnalysisContext
    from index.analysis.sentiment import IndexSentimentAnalyzer
    mocker.patch('akshare.stock_news_main_em', return_value=pd.DataFrame([{'title':'真实标题','pub_time':timestamp,'source':source}]), create=True)
    news = AkShareAdapter().fetch('000300', data_type='index_sentiment')[0]
    item = news.index_items[0]
    assert item['published_at'] is None
    if not isinstance(source, str):
        assert item['source'] == '未记录'
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type='index',symbol='000300',name='沪深300',index_style='broad'), news_items=news.index_items)
    assert IndexSentimentAnalyzer().analyze(ctx).status != 'ok'


def test_index_news_preserves_valid_date_and_fallback(mocker):
    mocker.patch('akshare.stock_news_main_em', return_value=pd.DataFrame([{'title':'真实标题','pub_time':float('nan'),'publish_time':'2026-10-29 09:30:00','source':'来源媒体'}]), create=True)
    item=AkShareAdapter().fetch('000300',data_type='index_sentiment')[0].index_items[0]
    assert item['published_at'] == '2026-10-29 09:30:00'
    assert item['source'] == '来源媒体'


def test_index_news_cache_preserves_known_endpoint_source(mocker, tmp_path):
    from index.request_cache import IndexRequestCache
    mocker.patch("akshare.stock_news_main_em", side_effect=AttributeError, create=True)
    mocker.patch("akshare.stock_news_main_cx", return_value=pd.DataFrame([{"title":"财新标题","pub_time":"2026-10-29 09:30:00","source":float("nan"),"url":"https://www.caixin.com/example"}]))
    cache=IndexRequestCache(tmp_path/"news-cache.db")
    first=cache.fetch_cached(AkShareAdapter(),"000300","index_sentiment")
    assert first.record is not None
    assert first.record.source == "财新要闻接口"
    assert first.rows[0].index_items[0]["source"] == "未记录"
    cached=cache.fetch_cached(AkShareAdapter(),"000300","index_sentiment")
    assert cached.record is not None
    assert cached.record.source == "财新要闻接口"
    assert cached.record.cache_status == "cache"


def test_index_news_old_cache_recovers_known_source_from_link(mocker, tmp_path):
    import json
    from datetime import datetime

    from index.request_cache import IndexRequestCache
    cache=IndexRequestCache(tmp_path/"old-cache.db")
    payload={"rows":[{"symbol":"000300","date":"2026-10-29","headlines":["标题"],"index_items":[{"title":"标题","source":"nan","published_at":"nan","url":"https://www.caixin.com/article"}]}],"record":{"source":"来源未记录","collected_at":datetime.now().astimezone().isoformat()}}
    cache.cache.put("index_sentiment","000300:","status",json.dumps(payload),300)
    result=cache.fetch_cached(mocker.Mock(),"000300","index_sentiment")
    assert result.record is not None
    assert result.record.source == "财新要闻（缓存原始链接）"

@pytest.mark.parametrize('symbol,provider', [('000016','csi'),('000905','csi'),('000852','csi'),('000688','csi'),('000001','csi'),('399001','cni'),('399673','cni')])
def test_existing_broad_indices_keep_verified_official_valuation(mocker,symbol,provider):
    from data.index_mapping import IndexMapping
    from data.schemas import IndexValuationData
    entry=IndexMapping().lookup(symbol)
    assert entry is not None and entry.provider == provider and entry.source_url
    source=mocker.patch('index.valuation_data.StrategyValuationProvider').return_value
    source.fetch.return_value=IndexValuationData(symbol=symbol,date=date(2026,9,30),pe_snapshot=10.95,pb=1.15,valuation_valid=False)
    result=AkShareAdapter().fetch(symbol,data_type='index_valuation')
    assert result and result[0].pe_snapshot == 10.95 and result[0].pe_ttm is None
    source.fetch.assert_called_once_with(symbol,provider=provider)

def test_verified_sse50_official_layout_keeps_valuation():
    from pathlib import Path

    from index.valuation_factsheet import parse_factsheet_text
    # 中证官方上证50单张，2026-10-29采样，保留2026-09-30两页真实文本布局。
    text=(Path(__file__).resolve().parents[1]/'resources/index/csi-000016-valuation-factsheet-20260930.txt').read_text(encoding='utf-8')
    result=parse_factsheet_text(text,'000016','csi',date(2026,10,29))
    assert result['pe_snapshot']==10.95
    assert result['pb']==1.15
    assert result['as_of']==date(2026,9,30)
