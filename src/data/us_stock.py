"""美股单标的取数：行情、财务、估值、新闻与名称/快照。

数据源为公开免费接口，单次请求超时十秒；不在本模块做多源拼接，只按顺序回退并
保留失败原因。美股本期只提供客观数据，不含行业同行对比、综合评分与交易信号。
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
            name = str(frame.iloc[0].get("SECURITY_NAME_ABBR") or "").strip()
            if name:
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


def us_history(ticker: str, start: date | None = None, end: date | None = None) -> list[PriceData]:
    """近一年前复权日线；新浪为主源，东财为不拼接的备用整份。"""
    today = datetime.now(HK_TZ).date()
    end_date = min(end or today, today)
    start_date = start or (end_date - timedelta(days=365))
    if start_date > end_date:
        raise ValueError("美股行情起始日期晚于结束日期")
    best: list[PriceData] = []
    errors: list[str] = []
    try:
        rows = _sina_history(ticker, start_date, end_date)
        if len(rows) >= 60:
            return rows
        best = rows
    except Exception as error:  # noqa: BLE001 — 第三方主源边界，切换到备用整份
        logger.warning("新浪美股行情失败 %s: %s", ticker, type(error).__name__)
        errors.append(f"新浪：{type(error).__name__}")
    try:
        rows = _eastmoney_history(ticker, start_date, end_date)
        if len(rows) > len(best):
            best = rows
    except Exception as error:  # noqa: BLE001 — 第三方备源边界，保留主源结果
        logger.warning("东财美股行情失败 %s: %s", ticker, type(error).__name__)
        errors.append(f"东财：{type(error).__name__}")
    if not best:
        raise ValueError("未取得美股行情" + ("；" + "；".join(errors) if errors else ""))
    return best


def _statement(ticker: str, statement: str) -> dict[str, dict[str, float]]:
    import akshare as ak

    frame: Any = ak.stock_financial_us_report_em(
        stock="BRK_B" if ticker == "BRK.B" else ticker, symbol=statement, indicator="单季报",
    )
    if frame is None or frame.empty:
        raise ValueError(f"美股{statement}为空")
    periods: dict[str, dict[str, float]] = {}
    for _, row in frame.iterrows():
        code = str(row.get("SECURITY_CODE") or "").strip().upper()
        secucode = str(row.get("SECUCODE") or "").strip().upper()
        expected = "BRK_B" if ticker == "BRK.B" else ticker
        if (code and code != expected) or (secucode and secucode.rsplit(".", 1)[0] != expected):
            raise ValueError(f"美股{statement}代码不匹配")
        period = _parse_date(row.get("REPORT_DATE"))
        item = str(row.get("STD_ITEM_CODE") or "").strip()
        amount = _number(row.get("AMOUNT"))
        if period is None or not item or amount is None:
            continue
        periods.setdefault(period.isoformat(), {})[item] = amount
    if not periods:
        raise ValueError(f"美股{statement}没有可解析的报告期")
    return periods


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


def us_financials(ticker: str, max_periods: int = 8) -> list[FinancialData]:
    """东财美股 F10 单季报：最近八个财季，报告期按财季月末归一。"""
    income = _statement(ticker, "综合损益表")
    balance = _statement(ticker, "资产负债表")
    cash = _statement(ticker, "现金流量表")
    periods = sorted(set(income) & (set(balance) | set(cash)))[-max_periods:]
    if len(periods) < 4:
        raise ValueError("美股单季报不足四期")

    rows: list[FinancialData] = []
    balance_keys = sorted(balance)

    def balance_for(period_key: str) -> tuple[dict[str, float], str]:
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
        financial_company = any(code.startswith(("001", "002")) for code in income_row)
        revenue = _row_value(income_row, "004001999", "004001001", "001008999", "002001999")
        net_profit = _row_value(income_row, "004015999", "004013999", "001015999", "002011999")
        gross_profit = _row_value(income_row, "004005999")
        gross_margin = (gross_profit / revenue
                        if not financial_company and revenue and revenue > 0 and gross_profit is not None else None)
        start = None  # 源接口没有期间起点，不能按自然季度推造。
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
            reporting_basis="US GAAP",
            amount_currency_verified=False,
            report_currency="",
            revenue_basis="单季营业收入",
            cash_flow_basis="reported" if _row_value(cash_row, "003999") is not None else "unknown",
            cash_flow_period_start=start,
            financial_source="东方财富美股F10",
            financial_selection_status="complete",
            financial_selection_note="",
            field_sources=field_sources,
        )
        rows.append(row)

    # ROE 按最近四个财季净利润 / 期末归母权益派生；资产负债表期不一致时不混算。
    for index, row in enumerate(rows):
        if index < 3 or "roe" in row.field_sources:
            continue
        window = rows[index - 3:index + 1]
        profits = [item.net_profit for item in window]
        equity = row.common_equity or row.total_equity
        if equity and equity > 0 and all(is_finite_number(value) for value in profits):
            row.roe = sum(value for value in profits if value is not None) / equity
            row.field_sources["roe"] = "按最近四个财季净利润/期末归母权益派生"

    latest = rows[-1]
    required = (latest.revenue, latest.net_profit, latest.total_assets,
                latest.total_equity, latest.operating_cash_flow)
    latest.financial_selection_status = "complete" if all(
        is_finite_number(value) for value in required) else "partial"
    latest.financial_selection_note = (
        f"报告期按财季月末展示（源报告日 {date.fromisoformat(periods[-1]).isoformat()}）；"
        "最近四个财季合计为 TTM；源接口未提供金额币种证据，不推断换汇或 ADR 每股比例"
    )
    if ticker == "BRK.B":
        latest.financial_selection_note += "；F10 代码变体 BRK_B，伯克希尔哈撒韦-B 公司整体报表"
    if latest.field_sources.get("total_assets"):
        latest.financial_selection_note += f"；{latest.field_sources['total_assets']}"
    else:
        latest.financial_selection_note += "。"
    if latest.roe is None:
        try:
            roe, roe_period = _annual_roe(ticker)
        except Exception as error:  # noqa: BLE001 — 年报指标边界，失败只保留空 ROE
            logger.warning("美股年报 ROE 获取失败 %s: %s", ticker, type(error).__name__)
        else:
            if roe is not None and roe_period is not None:
                latest.roe = roe
                latest.field_sources["roe"] = (
                    f"最新年报 ROE_AVG（{roe_period.isoformat()}）"
                )
    return rows


def _baidu_metric(ticker: str, metric: str) -> dict[date, float]:
    response = requests.get(BAIDU_URL, params={
        "openapi": "1", "dspName": "iphone", "tn": "tangram", "client": "app",
        "query": metric, "code": ticker, "word": "", "resource_id": "51171",
        "market": "us", "tag": metric, "chart_select": "近一年",
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
                    if period <= today and value is not None and value > 0:
                        points[period] = value
                except (ValueError, TypeError):
                    continue
        if not points:
            raise ValueError("百度美股估值缺少目标口径的合法日期指标")
        return points
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("百度美股估值响应结构异常") from error


def us_valuation(ticker: str) -> list[ValuationData]:
    """百度 PE(TTM)/PB 近一年曲线；快照取各自末值与日期，不伪造 PS。"""
    curves: dict[str, dict[date, float]] = {}
    failed: list[str] = []
    for metric, key in BAIDU_METRICS.items():
        try:
            points = _baidu_metric(ticker, metric)
            curves[key] = points
        except Exception as error:  # noqa: BLE001 — 第三方估值边界，保留另一指标
            logger.warning("百度美股%s获取失败 %s: %s", metric, ticker, type(error).__name__)
            failed.append(f"{key}_history")
    if not curves:
        raise ValueError("百度美股估值不可用")

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
        market_cap_as_of = _parse_date(str(quote.get("quoted_at") or "")[:10])
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
        market_cap=market_cap,
        market_cap_as_of=market_cap_as_of,
        pe_history=curves.get("pe", {}),
        pb_history=curves.get("pb", {}),
        history_source="百度美股估值（近一年）",
        valuation_notes=notes,
        cache_degraded=bool(failed),
        failed_components=list(failed),  # type: ignore[arg-type]
    )
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
    if not succeeded:
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
        values = [getattr(item, field) for item in rows]
        return sum(values) if all(is_finite_number(value) for value in values) else None
    return {
        "revenue": total("revenue"),
        "net_profit": total("net_profit"),
        "operating_cash_flow": total("operating_cash_flow"),
        "equity": rows[-1].common_equity or rows[-1].total_equity,
    }
