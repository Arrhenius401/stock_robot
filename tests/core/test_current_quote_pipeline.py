"""验证三市场管道使用共用报价入口，且独立报价不替换技术日线。"""
from datetime import date

import pytest

from core.pipeline import Pipeline
from core.registry import Registry
from data.schemas import AnalysisContext, PriceData
from utils.config import Config


@pytest.mark.parametrize("market,symbol", [("a-shares", "002714"), ("hk", "00700.HK"), ("us", "NVDA")])
def test_pipeline_uses_shared_quote_without_overwriting_daily(mocker, tmp_path, market, symbol):
    from data.fx_rates import FxRate

    if market == "hk":
        mocker.patch("data.fx_rates.average_rate",
                     side_effect=lambda c, s, e, target="CNY": FxRate(c, target, 1.0, (s, e)))
        mocker.patch("data.fx_rates.rate_on",
                     side_effect=lambda c, target="CNY", *, day: FxRate(c, target, 1.0, (day,)))
    row = PriceData(symbol=symbol, trade_date=date(2026, 11, 16),
                    open=46, high=47, low=45, close=46.3, volume=10)
    ctx = AnalysisContext(symbol=symbol, name=symbol, market=market, price_data=[row])
    pipeline = Pipeline(Registry(), Config(tmp_path), llm_enabled=False)
    mocker.patch.object(pipeline, "collect", return_value=ctx)
    fetch = mocker.patch("data.current_quote.collect_current_quote", return_value={
        "status": "ok", "price": 45.82, "quoted_at": "2026-11-17T16:14:24+08:00"})
    _, _, output = pipeline.run(symbol, symbol, market=market)
    fetch.assert_called_once_with(market, symbol, daily_end="2026-11-16")
    assert output.current_quote["price"] == 45.82
    assert output.price_data is not None
    assert output.price_data == [row] and output.price_data[0].close == 46.3
