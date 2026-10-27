"""测试数据源统一入口辅助。"""
from typing import Any, cast

from data.base import CollectionResult
from data.schemas import DataCollectionRecord


class FetchResultMixin:
    """为测试假数据源显式实现 fetch_result，满足五组能力契约。"""

    def fetch_result(self, symbol: str, **kwargs: Any) -> CollectionResult:
        fetch = getattr(self, "fetch", None)
        if not callable(fetch):
            raise TypeError("测试数据源必须实现 fetch")
        rows = cast(list[Any], fetch(symbol, **kwargs))
        reader = getattr(self, "get_collection_error", None)
        diagnostic = cast(
            DataCollectionRecord | None,
            reader() if callable(reader) else None,
        )
        return CollectionResult(rows=rows or [], record=diagnostic)
