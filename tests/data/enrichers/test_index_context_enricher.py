"""个股-指数联动充实器测试"""
import pytest
from unittest.mock import MagicMock, patch
from src.data.enrichers.index_context_enricher import IndexContextEnricher
from src.data.schemas import AnalysisContext


class TestIndexContextEnricher:
    def test_enrich_adds_market_environment(self):
        ctx = AnalysisContext(symbol="000001", name="平安银行", market="a-shares")

        mock_pipeline = MagicMock()
        mock_pipeline.get_snapshot.return_value = {
            "symbol": "000300",
            "pe_ttm": 12.5,
            "pe_percentile": 68.0,
            "valuation_valid": True,
        }

        enricher = IndexContextEnricher(mock_pipeline)
        result = enricher.enrich(ctx)

        assert result.market_environment is not None
        assert result.market_environment["symbol"] == "000300"
