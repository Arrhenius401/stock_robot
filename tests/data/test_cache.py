import json
import time
from pathlib import Path
from src.data.cache import CacheManager
from src.data.schemas import PriceData


class TestCacheManager:
    def test_put_and_get(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        data = PriceData(
            symbol="000001",
            trade_date=__import__("datetime").date(2026, 7, 1),
            open=10.0, high=11.0, low=9.5, close=10.5, volume=1000000,
        )
        cache.put("price", "000001", "2026-07-01", data.model_dump_json())
        result = cache.get("price", "000001", "2026-07-01")
        assert result is not None
        parsed = json.loads(result)
        assert parsed["close"] == 10.5

    def test_get_nonexistent_key_returns_none(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        assert cache.get("price", "000001", "2026-07-01") is None

    def test_get_expired_returns_none(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        data = PriceData(
            symbol="000001",
            trade_date=__import__("datetime").date(2026, 7, 1),
            open=10.0, high=11.0, low=9.5, close=10.5, volume=1000000,
        )
        cache.put("price", "000001", "2026-07-01", data.model_dump_json(), ttl_seconds=0)
        time.sleep(0.1)
        assert cache.get("price", "000001", "2026-07-01") is None

    def test_invalidate_removes_entry(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        cache.put("price", "000001", "2026-07-01", '{"test": true}')
        cache.invalidate("price", "000001", "2026-07-01")
        assert cache.get("price", "000001", "2026-07-01") is None

    def test_invalidate_by_symbol(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        cache.put("price", "000001", "2026-07-01", '{"a": 1}')
        cache.put("price", "000001", "2026-07-02", '{"a": 2}')
        cache.put("price", "000002", "2026-07-01", '{"a": 3}')
        cache.invalidate_symbol("000001")
        assert cache.get("price", "000001", "2026-07-01") is None
        assert cache.get("price", "000001", "2026-07-02") is None
        assert cache.get("price", "000002", "2026-07-01") is not None

    def test_clear_all(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        cache.put("price", "000001", "2026-07-01", '{"a": 1}')
        cache.put("financial", "000001", "2025Q4", '{"a": 2}')
        cache.clear()
        assert cache.get("price", "000001", "2026-07-01") is None
        assert cache.get("financial", "000001", "2025Q4") is None

    def test_stats(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        cache.put("price", "000001", "2026-07-01", '{"a": 1}')
        stats = cache.stats()
        assert stats["total_entries"] == 1
        assert stats["db_size_bytes"] > 0

    def test_default_ttl_used_when_not_specified(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db", default_ttls={"price": 0})
        cache.put("price", "000001", "2026-07-01", '{"a": 1}')
        time.sleep(0.1)
        assert cache.get("price", "000001", "2026-07-01") is None
