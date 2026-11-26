"""美股单标的取数：行情、财务、估值、新闻与名称/快照。

数据源为公开免费接口，单次请求超时十秒；不在本模块做多源拼接，只按顺序回退并
保留失败原因。行业经营比较与评分由独立数据层及分析管道按证据门槛处理。
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from itertools import pairwise
from typing import Any, cast
from zoneinfo import ZoneInfo

import requests

from data.collection_state import ValueResult, make_record
from data.hk_news import HK_TZ, search_news
from data.schemas import (
    FinancialData,
    NewsData,
    PriceData,
    RawSentimentData,
    RawSentimentItem,
    ValuationData,
)
from data.us_industry import _code, normalize_us_code
from utils.numbers import is_finite_number, parse_cn_number

logger = logging.getLogger(__name__)

SINA_QUOTE_URL = "https://hq.sinajs.cn/list=gb_{ticker}"
SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
BAIDU_URL = "https://finance.baidu.com/opendata"
BAIDU_METRICS = {"市盈率(TTM)": "pe", "市净率": "pb"}
_SINA_LINE = re.compile(r'var hq_str_gb_(?P<ticker>[a-z0-9.\-]+)="(?P<body>[^"]*)"', re.IGNORECASE)
_EASTMONEY_MARKETS = ("105", "106", "107")
US_TZ = ZoneInfo("America/New_York")
_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)
_EARLY_CLOSE = time(13, 0)
# 中文名与日常名词同形时，标题需要公司/市场语境，且排除农产品/期货等明显无关主题。
_AMBIGUOUS_NAMES = frozenset({"苹果"})
_NEWS_NEGATIVE = (
    "期货", "主力合约", "现货", "产地", "果园", "种植", "采摘", "批发",
    "菜市场", "红薯", "水果", "果汁", "农业", "田间", "商超",
)
_NEWS_CONTEXT = (
    "股价", "股票", "市值", "财报", "业绩", "营收", "净利", "利润", "涨", "跌",
    "新高", "新低", "回购", "分红", "拆股", "上市", "退市", "评级", "目标价",
    "分析师", "机构", "基金", "持仓", "减持", "增持", "收购", "并购", "诉讼",
    "监管", "罚款", "调查", "发布", "产品", "服务", "芯片", "iPhone", "手机",
    "AI", "云计算", "广告", "裁员", "CEO", "高管", "库克", "供应链",
    "以旧换新", "价格",
)


def _easter(year: int) -> date:
    """复活节星期日（Anonymous Gregorian algorithm）；用于 Good Friday 休市。"""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    length = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * length) // 451
    month = (h + length - 7 * m + 114) // 31
    day = (h + length - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


@lru_cache(maxsize=8)
def us_market_holidays(year: int) -> frozenset[date]:
    """NYSE 全日休市日；不含仅提前收盘的日期。"""
    return frozenset({
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),      # MLK
        _nth_weekday(year, 2, 0, 3),      # Washington's Birthday
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),        # Memorial Day
        _observed(date(year, 6, 19)),     # Juneteenth
        _observed(date(year, 7, 4)),      # Independence Day
        _nth_weekday(year, 9, 0, 1),      # Labor Day
        _nth_weekday(year, 11, 3, 4),     # Thanksgiving
        _observed(date(year, 12, 25)),    # Christmas
    })


@lru_cache(maxsize=8)
def us_market_early_closes(year: int) -> frozenset[date]:
    """NYSE 提前至 13:00 ET 收盘的常见日期；节假日优先级更高。"""
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    candidates = {
        thanksgiving + timedelta(days=1),
        date(year, 7, 3),
        date(year, 12, 24),
    }
    return frozenset(day for day in candidates if day.weekday() < 5)


def us_market_session(now: datetime | None = None) -> str:
    """返回 pre/open/closed；只有常规交易时段算 open。"""
    current = now or datetime.now(US_TZ)
    current = current.replace(tzinfo=US_TZ) if current.tzinfo is None else current.astimezone(US_TZ)
    day = current.date()
    holidays = us_market_holidays(day.year) | us_market_holidays(day.year + 1)
    if day.weekday() >= 5 or day in holidays:
        return "closed"
    if current.time() < _REGULAR_OPEN:
        return "pre"
    close = _EARLY_CLOSE if day in us_market_early_closes(day.year) else _REGULAR_CLOSE
    return "open" if current.time() < close else "closed"


def _field(fields: list[str], index: int) -> str:
    return fields[index].strip() if index < len(fields) else ""


def _month_end(day: date) -> date:
    if day.month == 12:
        return date(day.year, 12, 31)
    return date(day.year, day.month + 1, 1) - timedelta(days=1)


def _quarter_start(day: date) -> date:
    month = day.month - 2
    year = day.year
    if month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    number = parse_cn_number(value)
    return number if is_finite_number(number) else None


def us_quote(ticker: str) -> dict[str, Any]:
    """新浪美股快照：名称、最新价、涨跌幅、总市值与盘后字段。

    只读公开快照，不把盘后价当作收盘价；调用方决定使用哪些字段。
    """
    ticker = normalize_us_code(ticker)
    response = requests.get(
        SINA_QUOTE_URL.format(ticker=ticker.lower()), headers=SINA_HEADERS, timeout=10,
    )
    response.raise_for_status()
    text = response.content.decode("gbk", errors="replace")
    matched = _SINA_LINE.search(text)
    if matched is None or matched.group("ticker").lower() != ticker.lower():
        raise ValueError("新浪美股快照代码不匹配")
    fields = matched.group("body").split(",")
    if len(fields) < 13 or not _field(fields, 0):
        raise ValueError("新浪美股快照为空或字段不足")
    return {
        "symbol": ticker.upper(),
        "name": _field(fields, 0),
        "price": _number(_field(fields, 1)),
        "change_pct": _number(_field(fields, 2)),
        "quoted_at": _field(fields, 3),
        "market_cap": _number(_field(fields, 12)),
        "after_hours_price": _number(_field(fields, 21)),
        "after_hours_change_pct": _number(_field(fields, 22)),
        "after_hours_change": _number(_field(fields, 23)),
        "after_hours_time": _field(fields, 24),
        "close_time": _field(fields, 25),
    }


def us_name_result(ticker: str) -> ValueResult[str]:
    """名称优先新浪美股快照；失败回退东财美股指标表；仍失败则保留代码。"""
    try:
        quote = us_quote(ticker)
        name = str(quote.get("name") or "").strip()
        if name:
            return ValueResult(
                name,
                make_record("新浪美股快照", "complete", collected_at=datetime.now().astimezone()),
            )
    except Exception as error:  # noqa: BLE001 — 第三方快照边界，名称失败不影响分析
        logger.warning("新浪美股名称解析失败 %s: %s", ticker, type(error).__name__)

    try:
        import akshare as ak

        frame: Any = ak.stock_financial_us_analysis_indicator_em(
            symbol=ticker, indicator="年报",
        )
        if frame is not None and not frame.empty:
            row = frame.iloc[0]
            name = str(row.get("SECURITY_NAME_ABBR") or "").strip()
            code = str(row.get("SECURITY_CODE") or "").upper().replace("_", ".")
            if name and code == ticker.upper().replace("_", "."):
                return ValueResult(
                    name,
                    make_record("东方财富美股F10", "complete",
                                collected_at=datetime.now().astimezone()),
                )
    except Exception as error:  # noqa: BLE001 — 第三方指标表边界，名称失败不影响分析
        logger.warning("东财美股名称解析失败 %s: %s", ticker, type(error).__name__)

    return ValueResult(
        None,
        make_record("美股名称", "failed", error_kind="fetch_failed",
                    error_message="美股名称解析失败"),
    )


def _parse_price_frame(frame: Any, ticker: str, start: date, end: date,
                       source: str) -> list[PriceData]:
    rows: dict[date, PriceData] = {}
    conflicts: set[date] = set()
    for _, row in frame.iterrows():
        try:
            period = _parse_date(row.get("date", row.get("日期")))
            values = [_number(row.get(key, row.get(alias)))
                      for key, alias in (("open", "开盘"), ("high", "最高"), ("low", "最低"),
                                         ("close", "收盘"), ("volume", "成交量"))]
            if period is None or any(value is None for value in values):
                continue
            if not start <= period <= end:
                continue
            opening, high, low, close, volume = (float(value) for value in cast(list[float], values))
            if (min(opening, high, low, close) <= 0 or volume < 0
                    or not low <= min(opening, close) <= max(opening, close) <= high):
                continue
            item = PriceData(symbol=ticker, trade_date=period, open=opening, high=high,
                             low=low, close=close, volume=int(volume))
            item._data_source = source
            previous = rows.get(period)
            if previous is not None:
                if (previous.open, previous.high, previous.low, previous.close,
                        previous.volume) != (opening, high, low, close, int(volume)):
                    conflicts.add(period)
                continue
            rows[period] = item
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            logger.debug("跳过美股异常行情行 %s: %s", ticker, error)
    if conflicts:
        raise ValueError("同一日期存在冲突美股行情，拒收该来源整段历史")
    ordered = [rows[key] for key in sorted(rows)]
    for previous, current in pairwise(ordered):
        if previous.close > 0:
            current.change_pct = round((current.close / previous.close - 1) * 100, 2)
    return ordered


def _sina_history(ticker: str, start: date, end: date) -> list[PriceData]:
    import akshare as ak

    frame: Any = ak.stock_us_daily(symbol=ticker, adjust="qfq")
    if frame is None or frame.empty:
        return []
    return _parse_price_frame(frame, ticker, start, end, "新浪美股行情（前复权）")


def _eastmoney_history(ticker: str, start: date, end: date) -> list[PriceData]:
    import akshare as ak

    best: list[PriceData] = []
    for market in _EASTMONEY_MARKETS:
        try:
            frame: Any = ak.stock_us_hist(
                symbol=f"{market}.{ticker}", period="daily",
                start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
            )
            if frame is None or frame.empty:
                continue
            rows = _parse_price_frame(frame, ticker, start, end, "东方财富美股行情（前复权）")
            if len(rows) > len(best):
                best = rows
            if len(best) >= 60:
                break
        except Exception as error:  # noqa: BLE001 — 第三方备源边界，保留首选结果
            logger.warning("东财美股行情备源失败 %s/%s: %s", ticker, market, type(error).__name__)
    return best


def us_history(ticker: str, start: date | None = None, end: date | None = None,
               *, diagnostics: list[dict[str, Any]] | None = None) -> list[PriceData]:
    """近一年前复权日线；新浪为主源，东财为不拼接的备用整份。"""
    ticker = normalize_us_code(ticker)
    today = datetime.now(HK_TZ).date()
    end_date = min(end or today, today)
    start_date = start or (end_date - timedelta(days=365))
    if start_date > end_date:
        raise ValueError("美股行情起始日期晚于结束日期")
    best: list[PriceData] = []
    errors: list[str] = []
    sources = diagnostics if diagnostics is not None else []
    try:
        rows = _sina_history(ticker, start_date, end_date)
        sources.append({"source": "新浪美股行情", "status": "ok", "rows": len(rows), "error": ""})
        if len(rows) >= 60:
            return rows
        best = rows
    except Exception as error:  # noqa: BLE001 — 第三方主源边界，切换到备用整份
        logger.warning("新浪美股行情失败 %s: %s", ticker, type(error).__name__)
        errors.append(f"新浪：{type(error).__name__}")
        sources.append({"source": "新浪美股行情", "status": "failed", "rows": 0, "error": type(error).__name__})
    try:
        rows = _eastmoney_history(ticker, start_date, end_date)
        sources.append({"source": "东方财富美股行情", "status": "ok", "rows": len(rows), "error": ""})
        if len(rows) > len(best):
            best = rows
    except Exception as error:  # noqa: BLE001 — 第三方备源边界，保留主源结果
        logger.warning("东财美股行情失败 %s: %s", ticker, type(error).__name__)
        errors.append(f"东财：{type(error).__name__}")
        sources.append({"source": "东方财富美股行情", "status": "failed", "rows": 0, "error": type(error).__name__})
    if not best:
        raise ValueError("未取得美股行情" + ("；" + "；".join(errors) if errors else ""))
    return best


def _balance_statement(ticker: str) -> dict[str, dict[str, Any]]:
    code = _code(ticker)
    response = requests.get("https://datacenter.eastmoney.com/securities/api/data/v1/get", params={
        "reportName": "RPT_USF10_FN_BALANCE", "columns": "ALL", "source": "SECURITIES", "client": "PC",
        "filter": f'(SECURITY_CODE="{code}")', "pageSize": 500, "pageNumber": 1,
        "sortColumns": "REPORT_DATE", "sortTypes": "-1",
    }, timeout=10)
    response.raise_for_status()
    payload = response.json()
    rows = (payload.get("result") or {}).get("data")
    if payload.get("success") is not True or not isinstance(rows, list) or not rows:
        raise ValueError("美股资产负债表为空或响应失败")
    periods: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    today = datetime.now().astimezone().date()
    terminal_date = _parse_date(rows[-1].get("REPORT_DATE")) if len(rows) >= 500 else None
    for row in rows:
        secucode = str(row.get("SECUCODE") or "").split(".")
        if row.get("SECURITY_CODE") != code or len(secucode) != 2 or secucode[0] != code or secucode[1] not in {"N", "O", "A"}:
            raise ValueError("美股资产负债表身份不匹配")
        day = _parse_date(row.get("REPORT_DATE"))
        if day is None or day > today or day == terminal_date:
            continue
        key = day.isoformat()
        currency = FinancialData.normalize_report_currency(row.get("CURRENCY_ABBR"))
        notice = _parse_date(row.get("NOTICE_DATE"))
        if not currency or (notice is not None and notice > today):
            conflicts.add(key)
            continue
        item = str(row.get("STD_ITEM_CODE") or "").strip()
        amount = _number(row.get("AMOUNT"))
        if not item or amount is None:
            continue
        accounting = str(row.get("ACCOUNT_STANDARD") or "")
        entry = periods.setdefault(key, {"_meta": {"currency": currency, "accounting": accounting, "notice": ""}})
        metadata = entry["_meta"]
        if metadata["currency"] != currency or metadata["accounting"] != accounting or (item in entry and entry[item] != amount):
            conflicts.add(key)
        if notice:
            metadata["notice"] = max(metadata["notice"], notice.isoformat())
        entry[item] = amount
    return {key: row for key, row in periods.items() if key not in conflicts}


def _statement(ticker: str, statement: str) -> dict[str, dict[str, Any]]:
    if statement == "资产负债表":
        return _balance_statement(ticker)
    import akshare as ak

    frame: Any = ak.stock_financial_us_report_em(
        stock=_code(ticker), symbol=statement, indicator="单季报",
    )
    if frame is None or frame.empty:
        raise ValueError(f"美股{statement}为空")
    periods: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        code = str(row.get("SECURITY_CODE") or "").strip().upper()
        secucode = str(row.get("SECUCODE") or "").strip().upper()
        expected = _code(ticker)
        if (code and code != expected) or (secucode and secucode.rsplit(".", 1)[0] != expected):
            raise ValueError(f"美股{statement}代码不匹配")
        period = _parse_date(row.get("REPORT_DATE"))
        item = str(row.get("STD_ITEM_CODE") or "").strip()
        amount = _number(row.get("AMOUNT"))
        if period is None or not item or amount is None:
            continue
        entry = periods.setdefault(period.isoformat(), {})
        metadata = {"start": str(row.get("START_DATE") or "")[:10],
                    "currency": str(row.get("CURRENCY_ABBR") or ""),
                    "notice": str(row.get("NOTICE_DATE") or ""),
                    "accounting": str(row.get("ACCOUNT_STANDARD") or "")}
        if "_meta" in entry and entry["_meta"] != metadata:
            raise ValueError(f"美股{statement}同一期期间/币种冲突")
        if item in entry and entry[item] != amount:
            raise ValueError(f"美股{statement}同一期科目冲突")
        if any(metadata.values()):
            entry["_meta"] = metadata
        entry[item] = amount
        if statement == "综合损益表" and item == "004001999":
            yoy = _number(row.get("YOY_RATIO"))
            if yoy is not None:
                entry["YOY_RATIO"] = yoy
    if not periods:
        raise ValueError(f"美股{statement}没有可解析的报告期")
    metadata = _statement_metadata(ticker, statement, min(periods))
    for period, entry in periods.items():
        if period in metadata:
            entry["_meta"] = metadata[period]
    return periods


def _statement_metadata(ticker: str, statement: str, start: str) -> dict[str, dict[str, str]]:
    """包装器未请求期间和币种，向同一F10端点补取原字段，不按季度猜测。"""
    report = {"综合损益表": "RPT_USF10_FN_INCOME", "资产负债表": "RPT_USF10_FN_BALANCE",
              "现金流量表": "RPT_USSK_FN_CASHFLOW"}[statement]
    code = _code(ticker)
    try:
        response = requests.get("https://datacenter.eastmoney.com/securities/api/data/v1/get", params={
            "reportName": report, "columns": "ALL", "source": "SECURITIES", "client": "PC",
            "filter": f"(SECURITY_CODE=\"{code}\")(REPORT_DATE>='{start}')",
            "pageSize": 500, "pageNumber": 1, "sortColumns": "REPORT_DATE", "sortTypes": "-1",
        }, timeout=10)
        response.raise_for_status()
        payload = response.json()
        if payload.get("success") is not True:
            raise ValueError(str(payload.get("message") or "三表元信息响应失败"))
        result = payload.get("result") or {}
        rows = result.get("data") or []
        metadata: dict[str, dict[str, str]] = {}
        conflicts: set[str] = set()
        for row in rows:
            if row.get("SECURITY_CODE") != code:
                raise ValueError("三表元信息身份不匹配")
            if statement != "资产负债表" and row.get("REPORT_TYPE") != "单季报":
                continue
            period = str(row.get("REPORT_DATE") or "")[:10]
            item = {"start": str(row.get("START_DATE") or "")[:10],
                    "currency": str(row.get("CURRENCY_ABBR") or ""),
                    "accounting": str(row.get("ACCOUNT_STANDARD") or ""),
                    "notice": str(row.get("NOTICE_DATE") or "")}
            if period in metadata and metadata[period] != item:
                conflicts.add(period)
            metadata[period] = item
        return {key: value for key, value in metadata.items() if key not in conflicts}
    except (requests.RequestException, ValueError, TypeError, KeyError) as error:
        logger.warning("美股三表期间币种补取失败 %s/%s: %s", ticker, statement, error)
        return {}


def _row_value(period: dict[str, float], *codes: str) -> float | None:
    for code in codes:
        value = period.get(code)
        if value is not None:
            return value
    return None


def _annual_roe(ticker: str) -> tuple[float | None, date | None]:
    """最新年报 ROE_AVG（百分数转小数）；资产负债表期间不一致时的兜底。"""
    import akshare as ak

    frame: Any = ak.stock_financial_us_analysis_indicator_em(
        symbol=ticker, indicator="年报",
    )
    if frame is None or frame.empty:
        raise ValueError("美股年报指标为空")
    records = []
    for _, row in frame.iterrows():
        period = _parse_date(row.get("REPORT_DATE"))
        roe = _number(row.get("ROE_AVG"))
        if period is not None and roe is not None:
            records.append((period, roe))
    if not records:
        raise ValueError("美股年报缺少有效 ROE_AVG")
    period, roe = max(records)
    return roe / 100, period


def us_financials(ticker: str, max_periods: int = 8, *, refresh_cache: bool = False) -> list[FinancialData]:
    """东财美股 F10 单季报：最近八个财季，报告期按财季月末归一。"""
    ticker = normalize_us_code(ticker)
    income = _statement(ticker, "综合损益表")
    try:
        balance = _statement(ticker, "资产负债表")
    except (requests.RequestException, ValueError, TypeError, KeyError) as error:
        logger.warning("美股资产负债表失败 %s: %s", ticker, error)
        balance = {}
    cash = _statement(ticker, "现金流量表")
    periods = sorted(set(income) & (set(balance) | set(cash)))[-max_periods:]
    if not periods:
        raise ValueError("美股单季报没有可用报告期")

    rows: list[FinancialData] = []
    balance_keys = sorted(balance)

    def balance_for(period_key: str) -> tuple[dict[str, Any], str]:
        exact = balance.get(period_key)
        if exact is not None:
            return exact, period_key
        candidates = [key for key in balance_keys if key <= period_key]
        if not candidates:
            return {}, ""
        latest_key = candidates[-1]
        return balance.get(latest_key, {}), latest_key

    for period_key in periods:
        report_date = date.fromisoformat(period_key)
        fiscal = _month_end(report_date)
        income_row = income.get(period_key, {})
        balance_row, balance_key = balance_for(period_key)
        cash_row = cash.get(period_key, {})
        financial_company = any(code.startswith(("001", "002")) for code in income_row if not code.startswith("_"))
        revenue = _row_value(income_row, "004001999", "004001001", "001008999", "002001999")
        if ticker in {"BRK.B", "BRK_B"}:
            revenue = None
        net_profit = _row_value(income_row, "004015999", "004013999", "001015999", "002011999")
        gross_profit = _row_value(income_row, "004005999")
        gross_margin = (gross_profit / revenue
                        if not financial_company and revenue and revenue > 0 and gross_profit is not None else None)
        income_meta = income_row.get("_meta", {})
        cash_meta = dict(cash_row.get("_meta", {}))
        cash_periods = sorted(cash)
        cash_index = cash_periods.index(period_key) if period_key in cash else 0
        if cash_meta and cash_index and not cash_meta.get("start"):
            previous_end = date.fromisoformat(cash_periods[cash_index - 1])
            if 70 <= (report_date - previous_end).days <= 113:
                cash_meta["start"] = (previous_end + timedelta(days=1)).isoformat()
        balance_meta = balance_row.get("_meta", {})
        start = _parse_date(income_meta.get("start"))
        currency = income_meta.get("currency", "")
        cash_start = _parse_date(cash_meta.get("start"))
        field_sources: dict[str, str] = {}
        if balance_key and balance_key != period_key:
            balance_note = f"资产负债表最近可得 {balance_key}"
            for name in ("total_assets", "total_equity", "common_equity", "roe"):
                field_sources[name] = balance_note
        capex_parts = [cash_row.get(code) for code in ("005002", "005004")]
        capex = sum(abs(value) for value in capex_parts if value is not None) if any(value is not None for value in capex_parts) and not financial_company else None
        ocf = cash_row.get("003999")
        row = FinancialData(
            symbol=ticker,
            fiscal_quarter=fiscal,
            revenue=revenue,
            net_profit=net_profit,
            total_assets=_row_value(balance_row, "004005999", "001001999", "002001999"),
            total_equity=_row_value(balance_row, "004017999", "001009999", "002009999"),
            common_equity=_row_value(balance_row, "004013999", "001005999", "002005999"),
            operating_cash_flow=ocf,
            capital_expenditure=capex,
            free_cash_flow=ocf - capex if ocf is not None and capex is not None else None,
            basic_eps=_row_value(income_row, "004017003", "001017003", "002013003"),
            gross_margin=gross_margin,
            gross_margin_basis=("银行/保险不适用" if financial_company else
                                "毛利/营业收入（单季）" if gross_margin is not None else ""),
            period_start=start,
            period_basis="single",
            reporting_basis="东方财富美股F10-单季报（会计准则未核实）",
            amount_currency_verified=bool(currency),
            report_currency=currency,
            revenue_basis="单季营业收入",
            cash_flow_basis="reported" if _row_value(cash_row, "003999") is not None else "unknown",
            cash_flow_period_start=cash_start,
            financial_source="东方财富美股F10",
            financial_selection_status="complete",
            financial_selection_note="",
            field_sources=field_sources,
        )
        for field in ("revenue", "net_profit", "total_assets", "total_equity", "common_equity", "operating_cash_flow", "capital_expenditure", "free_cash_flow"):
            source_period = balance_key if field in {"total_assets", "total_equity", "common_equity"} else period_key
            period_note = f"，源报告日 {source_period or '未知'}" if field in {"total_assets", "total_equity", "common_equity"} else ""
            row.field_sources.setdefault(field, "东方财富美股F10" + period_note + "；" + ("未披露/不适用" if getattr(row, field) is None else "源科目映射/已披露现金支出派生"))
        for field in ("net_profit", "revenue", "operating_cash_flow", "total_assets", "common_equity", "total_equity"):
            meta = cash_meta if field == "operating_cash_flow" else balance_meta if field in {"total_assets", "common_equity", "total_equity"} else income_meta
            instant = field in {"total_assets", "common_equity", "total_equity"}
            row.field_positions[field] = {
                "definition": "parent_net_profit" if field == "net_profit" else field,
                "start": "" if instant else meta.get("start", ""),
                "end": balance_key if instant else period_key,
                "currency": meta.get("currency", ""), "unit": "currency",
                "basis": "instant" if instant else "single", "scope": "consolidated",
                "calculation": "reported", "accounting": meta.get("accounting", ""),
                "revision": "当前源快照", "notice": meta.get("notice", ""),
            }
        if row.revenue is not None and row.revenue > 0 and row.net_profit is not None and start is not None:
            row.net_margin = row.net_profit / row.revenue
            row.net_margin_basis = "计算值：单季净利润÷营业收入；仅展示"
        row.precision_version = 1
        # F10 的 YOY_RATIO 以百分数披露；缺失时由金额可比契约决定能否复算。
        reported_yoy = _number(income_row.get("YOY_RATIO"))
        if reported_yoy is not None:
            row.reported_metrics["revenue_growth_yoy"] = reported_yoy / 100
        row.field_precision = {field: "source_native" for field in (
            "revenue", "net_profit", "total_assets", "total_equity", "common_equity",
            "operating_cash_flow", "capital_expenditure", "free_cash_flow", "gross_margin", "basic_eps")
            if is_finite_number(getattr(row, field))}
        rows.append(row)

    # ROE 按最近四个财季净利润 / 期末归母权益派生；资产负债表期不一致时不混算。
    for index, row in enumerate(rows):
        if index < 3:
            continue
        window = rows[index - 3:index + 1]
        profits = [item.net_profit for item in window]
        equity = row.common_equity if row.common_equity is not None else row.total_equity
        if equity and equity > 0 and all(is_finite_number(value) for value in profits):
            row.roe = sum(value for value in profits if value is not None) / equity
            row.field_sources["roe"] = "按最近四个财季归母净利润/最近可得期末归母权益派生；分母日期 " + balance_for(periods[index])[1]

    latest = rows[-1]
    required = (latest.revenue, latest.net_profit, latest.total_assets,
                latest.total_equity, latest.operating_cash_flow)
    latest.financial_selection_status = "complete" if all(
        is_finite_number(value) for value in required) else "partial"
    latest.financial_selection_note = (
        f"报告期按财季月末展示（源报告日 {date.fromisoformat(periods[-1]).isoformat()}）；"
        "最近四个财季合计为 TTM；期间/币种按源三表逐行保留，缺失不推断；不推断换汇或 ADR 每股比例"
    )
    if len(rows) < 4:
        latest.financial_selection_status = "partial"
        latest.financial_selection_note = (
            f"仅有 {len(rows)} 个财季；TTM/ROE/PS 需四个财季，暂不计算；"
            "期间/币种按源三表逐行保留，缺失不推断"
        )
    if ticker == "BRK.B":
        latest.financial_selection_note += "；F10 代码变体 BRK_B，伯克希尔哈撒韦-B 公司整体报表"
    if latest.field_sources.get("total_assets"):
        latest.financial_selection_note += f"；{latest.field_sources['total_assets']}"
    else:
        latest.financial_selection_note += "。"
    if ticker == "BRK.B":
        from data.us_sec import load_brk_revenue
        try:
            facts = load_brk_revenue(refresh=refresh_cache)
        except Exception as error:  # noqa: BLE001 — SEC 展示事实边界
            logger.warning("BRK.B SEC 营业收入失败: %s", type(error).__name__)
            facts = []
            rows[-1].financial_selection_note += "；SEC 营业收入未取得或未对账通过（" + type(error).__name__ + "）"
        by_period = {fact.end: fact for fact in facts}
        for row in rows:
            fact = by_period.get(row.fiscal_quarter)
            if fact is None:
                continue
            row.reported_revenue = fact.value
            row.reported_revenue_period = f"{fact.start.isoformat()} 至 {fact.end.isoformat()}"
            row.reported_revenue_source = f"SEC XBRL {fact.concept}（{fact.form}，filed {fact.filed.isoformat()}）"
            if fact.cache_stale:
                row.reported_revenue_source += "；旧成功缓存（本次 SEC 刷新失败，保留原日期）"
            row.reported_revenue_basis = "营业收入（不含投资损益）"
        last4 = [by_period.get(row.fiscal_quarter) for row in rows[-4:]]
        if (len(last4) == 4 and all(item is not None for item in last4)
                and all(b.start == a.end + timedelta(days=1) for a, b in pairwise(last4)
                        if a is not None and b is not None)):
            rows[-1].reported_revenue_ttm = sum(item.value for item in last4 if item is not None)

    return rows


def _baidu_metric(ticker: str, metric: str, chart_select: str = "近一年") -> dict[date, float]:
    response = requests.get(BAIDU_URL, params={
        "openapi": "1", "dspName": "iphone", "tn": "tangram", "client": "app",
        "query": metric, "code": ticker, "word": "", "resource_id": "51171",
        "market": "us", "tag": metric, "chart_select": chart_select,
        "industry_select": "", "skip_industry": "1", "finClientType": "pc",
    }, timeout=10)
    response.raise_for_status()
    try:
        result = response.json()["Result"][0]["DisplayData"]["resultData"]["tplData"]["result"]
        if not isinstance(result, dict):
            raise TypeError("百度美股结果结构异常")
        if str(result.get("code") or "").upper() != ticker.upper():
            raise ValueError("百度美股代码不匹配")
        charts = result["chartInfo"]
        if not isinstance(charts, list):
            raise TypeError("百度美股估值曲线结构异常")
        points: dict[date, float] = {}
        today = datetime.now(HK_TZ).date()
        for chart in charts:
            if not isinstance(chart, dict) or chart.get("header") != [metric]:
                continue
            body = chart.get("body")
            if not isinstance(body, list):
                continue
            for item in body:
                try:
                    if not isinstance(item, list) or len(item) != 2:
                        continue
                    period = date.fromisoformat(str(item[0]))
                    value = _number(item[1])
                except (ValueError, TypeError):
                    continue
                if period <= today and value is not None and value > 0:
                    if chart_select != "近一年" and period in points:
                        raise ValueError("百度长窗估值日期重复")
                    points[period] = value
        if not points:
            raise ValueError("百度美股估值缺少目标口径的合法日期指标")
        return points
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("百度美股估值响应结构异常") from error


def _tencent_pb(ticker: str) -> tuple[float | None, date | None]:
    """腾讯字段 51 仅作为 PB 第二快照，校验代码与源日期。"""
    response = requests.get(f"https://qt.gtimg.cn/q=us{ticker}", timeout=10)
    response.raise_for_status()
    matched = re.search(r'v_us([^=]+)="([^"]*)"', response.content.decode("gbk", errors="replace"))
    if matched is None or matched[1].upper() != ticker.upper():
        raise ValueError("腾讯美股快照代码不匹配")
    fields = matched[2].split("~")
    raw_date = _field(fields, 30)
    stamp = _parse_date(raw_date)
    if stamp is None and len(raw_date) >= 8:
        stamp = _parse_date(f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}")
    value = _number(_field(fields, 51))
    return (value, stamp) if value is not None and stamp is not None else (None, None)


def us_valuation(ticker: str) -> list[ValuationData]:
    """百度 PE(TTM)/PB 近一年曲线；快照取各自末值与日期，不伪造 PS。"""
    ticker = normalize_us_code(ticker)
    curves: dict[str, dict[date, float]] = {}
    failed: list[str] = []
    for metric, key in BAIDU_METRICS.items():
        try:
            points = _baidu_metric(ticker, metric)
            curves[key] = points
        except Exception as error:  # noqa: BLE001 — 第三方估值边界，保留另一指标
            logger.warning("百度美股%s获取失败 %s: %s", metric, ticker, type(error).__name__)
            failed.append(f"{key}_history")

    def latest(key: str) -> tuple[float | None, date | None]:
        points = curves.get(key) or {}
        if not points:
            return None, None
        period = max(points)
        return points[period], period

    pe, pe_as_of = latest("pe")
    pb, pb_as_of = latest("pb")
    market_cap = None
    market_cap_as_of = None
    try:
        quote = us_quote(ticker)
        raw_cap = quote.get("market_cap")
        market_cap = raw_cap if is_finite_number(raw_cap) else None
        market_cap_as_of = (_parse_date(str(quote.get("close_time") or "")[:10])
                            or _parse_date(str(quote.get("quoted_at") or "")[:10]))
    except Exception as error:  # noqa: BLE001 — 快照边界，PS 派生失败不影响 PE/PB
        logger.warning("美股总市值快照失败 %s: %s", ticker, type(error).__name__)
    dates = [value for value in (pe_as_of, pb_as_of) if value is not None]
    notes = []
    if pe is not None and pe_as_of is not None:
        notes.append(f"PE(TTM) 快照：百度美股估值（{pe_as_of.isoformat()}）")
    if pb is not None and pb_as_of is not None:
        notes.append(f"PB 快照：百度美股估值（{pb_as_of.isoformat()}）")
    notes.append("历史分位按百度近一年曲线与行情交易日对齐；历史低分位不等于低风险")
    if market_cap is not None:
        notes.append(
            f"PS(TTM)：新浪美股快照总市值（{market_cap_as_of.isoformat() if market_cap_as_of else '日期未记录'}）"
            "÷ 东财 TTM 营收派生，仅披露"
        )
    else:
        notes.append("PS(TTM)：未取得新浪总市值，暂不派生")
    item = ValuationData(
        symbol=ticker,
        date=max(dates) if dates else datetime.now(HK_TZ).date(),
        pe_ttm=pe,
        pb=pb,
        pe_as_of=pe_as_of,
        pb_as_of=pb_as_of,
        pb_source="百度美股估值" if pb is not None else "",
        market_cap=market_cap,
        market_cap_as_of=market_cap_as_of,
        pe_history=curves.get("pe", {}),
        pb_history=curves.get("pb", {}),
        history_source="百度美股估值（近一年）",
        valuation_notes=notes,
        cache_degraded=bool(failed),
        failed_components=list(failed),  # type: ignore[arg-type]
    )
    from data.dividend_yield import us_dividend_yield

    dividend = us_dividend_yield(ticker)
    item.dividend_yield = dividend.value
    item.dividend_yield_as_of = dividend.as_of
    item.dividend_yield_source = dividend.source
    item.dividend_yield_basis = dividend.basis
    if dividend.value is None:
        notes.append(f"股息率：{dividend.reason}")
    now = datetime.now(US_TZ)
    cutoff = now.date()
    if now.time() < (_EARLY_CLOSE if cutoff in us_market_early_closes(cutoff.year) else _REGULAR_CLOSE):
        cutoff -= timedelta(days=1)
    for metric, key in BAIDU_METRICS.items():
        try:
            points = _baidu_metric(ticker, metric, "近五年")
            eligible = {day: value for day, value in points.items() if day <= cutoff
                        and day.weekday() < 5 and day not in us_market_holidays(day.year)}
            baseline = curves.get(key, {})
            if any(eligible[day] != baseline[day] for day in eligible.keys() & baseline.keys() if day < now.date()):
                raise ValueError("百度长窗与短窗共同历史日估值不同")
            if eligible:
                setattr(item, f"extended_{key}_history", eligible)
        except (requests.RequestException, TypeError, ValueError) as error:
            logger.warning("百度美股长窗估值失败 %s/%s: %s", ticker, metric, error)
    if item.extended_pe_history or item.extended_pb_history:
        item.history_window_days = 1827
        item.history_window_label = "近五年"
    else:
        item.valuation_notes.append("百度五年估值暂不可用，保留原近一年数据")
    if not item.pb_history:
        start = now.date() - timedelta(days=365)
        item.pb_history = {day: value for day, value in item.extended_pb_history.items() if day >= start}
        if item.pb_history:
            item.pb_as_of = max(item.pb_history)
            item.pb = item.pb_history[item.pb_as_of]
            item.pb_source = "百度美股估值"
            item.failed_components = [key for key in item.failed_components if key != "pb_history"]
            item.cache_degraded = bool(item.failed_components)
            item.valuation_notes.append("近一年PB失败，使用同源五年曲线最近一年切片")
        elif not item.extended_pb_history:
            try:
                item.pb, item.pb_as_of = _tencent_pb(ticker)
                if item.pb is not None:
                    item.pb_source = "腾讯美股快照PB"
                    item.valuation_notes.append("腾讯美股快照PB；无同源历史，不计算历史分位")
            except Exception as error:  # noqa: BLE001 — 第三方快照边界
                logger.warning("腾讯美股PB失败 %s: %s", ticker, error)
    if item.pe_ttm is None and item.pb is None and not (item.market_cap is not None and is_finite_number(item.market_cap) and item.market_cap > 0):
        raise ValueError("美股估值不可用")
    snapshot_dates = [day for day in (item.pe_as_of, item.pb_as_of) if day is not None]
    if snapshot_dates:
        item.date = max(snapshot_dates)
    item._data_source = "百度美股估值"
    return [item]


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", value)).strip()


def _safe_url(value: str) -> bool:
    return value.startswith(("https://", "http://")) and "\n" not in value and "\r" not in value


def us_news(ticker: str, name: str = "") -> list[NewsData]:
    """东财关键词检索：中文名 + 代码，近 30 天，最多 20 条，仅作新闻参考。"""
    now = datetime.now(HK_TZ)
    today = now.date()
    start = today - timedelta(days=29)
    queries = [ticker]
    candidate_name = name.strip()
    if not candidate_name:
        try:
            candidate_name = str(us_quote(ticker).get("name") or "").strip()
        except Exception as error:  # noqa: BLE001 — 名称快照边界，仅影响检索词
            logger.debug("美股新闻名称补取失败 %s: %s", ticker, type(error).__name__)
    if candidate_name and candidate_name.upper() != ticker.upper() and not candidate_name.isdigit():
        queries.append(candidate_name)
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    succeeded = 0
    for query in queries:
        try:
            rows.extend(search_news(query))
            succeeded += 1
        except Exception as error:  # noqa: BLE001 — 单关键词检索边界，保留部分成功
            logger.warning("美股新闻查询失败 %s/%s: %s", ticker, query, type(error).__name__)
            errors.append(query)
    from data.us_filings import fetch_filings

    announcements = []
    try:
        announcements = fetch_filings(ticker)
    except Exception as error:  # noqa: BLE001 — SEC 独立来源边界，保留新闻结果
        logger.warning("SEC 公告失败 %s: %s", ticker, type(error).__name__)
    if not succeeded and not announcements:
        raise RuntimeError("美股新闻暂时获取失败")

    items: list[RawSentimentItem] = []
    seen: set[tuple[str, str]] = set()
    seen_urls: set[str] = set()
    for row in rows:
        try:
            stamp = datetime.fromisoformat(str(row.get("date") or ""))
            stamp = stamp.replace(tzinfo=HK_TZ) if stamp.tzinfo is None else stamp.astimezone(HK_TZ)
            title = _clean_text(str(row.get("title") or ""))
            content = _clean_text(str(row.get("content") or ""))[:500]
            url = str(row.get("url") or "")
            if not title or not _safe_url(url):
                continue
            ticker_hit = ticker.upper() in title.upper()
            name_hit = bool(candidate_name) and candidate_name in title
            if not ticker_hit and not name_hit:
                continue
            if name_hit and not ticker_hit and candidate_name in _AMBIGUOUS_NAMES:
                if any(word in title for word in _NEWS_NEGATIVE):
                    continue
                if not any(word in title for word in _NEWS_CONTEXT):
                    continue
            if not start <= stamp.date() <= today or stamp > now:
                continue
            key = (stamp.date().isoformat(), title)
            if key in seen or url in seen_urls:
                continue
            seen.add(key)
            seen_urls.add(url)
            items.append(RawSentimentItem(
                title=title,
                source="news",
                publish_date=stamp.date(),
                content=content,
                url=url,
                media=_clean_text(str(row.get("mediaName") or "来源未标注")),
            ))
        except (ValueError, TypeError):
            continue
    items.sort(key=lambda item: item.publish_date, reverse=True)
    items = items[:20]
    result = NewsData(symbol=ticker, date=today, headlines=[item.title for item in items])
    result.announcement_items = announcements
    result._raw_sentiment = RawSentimentData(symbol=ticker, fetch_date=today, items=items)
    result._data_source = "东方财富关键词新闻检索"
    if errors:
        logger.warning("美股新闻部分关键词失败 %s: %s", ticker, ",".join(errors))
    return [result]


def us_ttm(financials: list[FinancialData]) -> dict[str, float | None]:
    """最近四个财季合计；不足四期返回空值，不补造。"""
    rows = sorted(financials, key=lambda item: item.fiscal_quarter)[-4:]
    if len(rows) < 4:
        return {"revenue": None, "net_profit": None, "operating_cash_flow": None,
                "equity": rows[-1].common_equity or rows[-1].total_equity if rows else None}
    def total(field: str) -> float | None:
        from data.financial_metrics import native_amounts

        if not native_amounts(rows, (field,)):
            return None
        values = [getattr(item, field) for item in rows]
        return sum(values) if all(is_finite_number(value) for value in values) else None
    return {
        "revenue": total("revenue"),
        "net_profit": total("net_profit"),
        "operating_cash_flow": total("operating_cash_flow"),
        "equity": rows[-1].common_equity or rows[-1].total_equity,
    }
