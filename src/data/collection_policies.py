"""五组能力的业务策略：只判断有效性和结果状态，不管理缓存。"""
import math
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from data.hk_news import HK_TZ
from data.schemas import DataCollectionRecord, NewsData, ResultState
from data.validation import financial_has_facts


class CollectionPolicy(Protocol):
    kind: str
    ttl_key: str
    reference_age: int
    source_attempts: int

    def validate(self, symbol: str, rows: list[Any], now: datetime) -> bool: ...

    def classify(
        self,
        symbol: str,
        rows: list[Any],
        diagnostic: DataCollectionRecord | None,
        previous_rows: list[Any] | None,
        now: datetime,
    ) -> ResultState: ...

    def reference_rows(self, rows: list[Any], now: datetime) -> list[Any] | None: ...

    def fetch_constraints(self, previous_rows: list[Any] | None, now: datetime) -> dict[str, Any]: ...


class _Policy:
    source_attempts = 2

    def reference_rows(self, rows: list[Any], now: datetime) -> list[Any] | None:
        return rows

    def fetch_constraints(self, previous_rows: list[Any] | None, now: datetime) -> dict[str, Any]:
        return {}

    @staticmethod
    def _not_applicable(diagnostic: DataCollectionRecord | None) -> bool:
        return diagnostic is not None and diagnostic.cache_state == "not_applicable"


class FinancialPolicy(_Policy):
    kind = "financial"
    ttl_key = "quarterly"
    reference_age = 30 * 86400

    def __init__(self, market: str):
        self.market = market

    def validate(self, symbol: str, rows: list[Any], now: datetime) -> bool:
        today = now.astimezone(HK_TZ).date()
        if not rows or any(row.symbol != symbol for row in rows):
            return False
        if not financial_has_facts(rows, symbol):
            return False
        if len({row.fiscal_quarter for row in rows}) != len(rows):
            return False
        return all(row.fiscal_quarter <= today for row in rows)

    def classify(
        self,
        symbol: str,
        rows: list[Any],
        diagnostic: DataCollectionRecord | None,
        previous_rows: list[Any] | None,
        now: datetime,
    ) -> ResultState:
        if self._not_applicable(diagnostic) and not rows:
            return "not_applicable"
        if not self.validate(symbol, rows, now):
            return "failed"
        if self._not_applicable(diagnostic):
            return "not_applicable"
        latest = max(rows, key=lambda row: row.fiscal_quarter)
        if latest.financial_selection_status == "complete":
            return "complete"
        if latest.financial_selection_status == "partial":
            return "partial"
        from data.financial_selection import FinancialPackage
        if self.market == "hk":
            from data.hk_financial_policy import package_complete
        else:
            from data.a_financial_policy import package_complete
        package = FinancialPackage("缓存", rows, {})
        return "complete" if package_complete(package) else "partial"


class PricePolicy(_Policy):
    kind = "price"
    ttl_key = "daily"
    reference_age = 7 * 86400

    def __init__(self, market: str):
        self.market = market

    def validate(self, symbol: str, rows: list[Any], now: datetime) -> bool:
        today = now.astimezone(HK_TZ).date()
        if not rows or any(row.symbol != symbol for row in rows):
            return False
        if len({row.trade_date for row in rows}) != len(rows):
            return False
        return all(
            row.trade_date <= today
            and all(math.isfinite(value) and value >= 0 for value in
                    (row.open, row.high, row.low, row.close, row.volume))
            and min(row.open, row.close, row.low) > 0
            and row.high >= max(row.open, row.close, row.low)
            and row.low <= min(row.open, row.close, row.high)
            for row in rows
        )

    def _requirements(self, previous_rows: list[Any] | None, now: datetime) -> tuple[int, date | None]:
        if not previous_rows:
            return 60, None
        today = now.astimezone(HK_TZ).date()
        start = today - timedelta(days=365 if self.market == "hk" else 250)
        periods = [row.trade_date for row in previous_rows if start <= row.trade_date <= today]
        if not periods:
            return 60, None
        return max(60, len(periods)), max(periods)

    def classify(
        self,
        symbol: str,
        rows: list[Any],
        diagnostic: DataCollectionRecord | None,
        previous_rows: list[Any] | None,
        now: datetime,
    ) -> ResultState:
        if self._not_applicable(diagnostic) and not rows:
            return "not_applicable"
        if not self.validate(symbol, rows, now):
            return "failed"
        if self._not_applicable(diagnostic):
            return "not_applicable"
        minimum_points, minimum_end = self._requirements(previous_rows, now)
        if len(rows) < minimum_points:
            return "partial"
        if minimum_end is not None and max(row.trade_date for row in rows) < minimum_end:
            return "partial"
        return "complete"

    def fetch_constraints(self, previous_rows: list[Any] | None, now: datetime) -> dict[str, Any]:
        points, end = self._requirements(previous_rows, now)
        result: dict[str, Any] = {"minimum_points": points}
        if end is not None:
            result["minimum_end_date"] = end
        return result


class NewsPolicy(_Policy):
    kind = "news"
    ttl_key = "news"
    reference_age = 86400

    def validate(self, symbol: str, rows: list[Any], now: datetime) -> bool:
        today = now.astimezone(HK_TZ).date()
        if len(rows) != 1:
            return False
        news = rows[0]
        if not isinstance(news, NewsData) or news.symbol != symbol or news.date > today:
            return False
        raw = news._raw_sentiment
        if raw is None or raw.symbol != symbol:
            return False
        return all(item.publish_date <= today for item in raw.items)

    def classify(
        self,
        symbol: str,
        rows: list[Any],
        diagnostic: DataCollectionRecord | None,
        previous_rows: list[Any] | None,
        now: datetime,
    ) -> ResultState:
        if self._not_applicable(diagnostic) and not rows:
            return "not_applicable"
        if not self.validate(symbol, rows, now):
            return "failed"
        if self._not_applicable(diagnostic):
            return "not_applicable"
        raw = rows[0]._raw_sentiment
        assert raw is not None
        has_error = bool(diagnostic and (diagnostic.error_kind or diagnostic.error_message))
        if not raw.items:
            return "failed" if has_error else "empty"
        return "partial" if has_error else "complete"

    def reference_rows(self, rows: list[Any], now: datetime) -> list[Any] | None:
        if not rows:
            return None
        news = rows[0].model_copy(deep=True)
        raw = news._raw_sentiment
        if raw is None:
            return None
        end = now.astimezone(HK_TZ).date()
        raw.items = [
            item for item in raw.items
            if end - timedelta(days=29) <= item.publish_date <= end
        ]
        news.headlines = [item.title for item in raw.items]
        return [news]


POLICIES: dict[tuple[str, str], CollectionPolicy] = {
    ("a-shares", "financial"): FinancialPolicy("a-shares"),
    ("hk", "financial"): FinancialPolicy("hk"),
    ("a-shares", "price"): PricePolicy("a-shares"),
    ("hk", "price"): PricePolicy("hk"),
    ("a-shares", "news"): NewsPolicy(),
}


def policy_for(market: str, kind: str) -> CollectionPolicy | None:
    return POLICIES.get((market, kind))
