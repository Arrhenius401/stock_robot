"""策略指数公开数据采集：有限并发、请求超时及一天缓存。"""
import hashlib
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from io import BytesIO
from itertools import pairwise
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from data.cache import CacheManager
from data.index_mapping import IndexMapping
from data.industry_classifier import IndustryClassifier
from data.schemas import (
    AnalysisTarget,
    AnnualFinancialSnapshot,
    ConstituentSnapshot,
    IndexPriceData,
    StrategySnapshot,
)

logger = logging.getLogger(__name__)


def finite_number(value: Any) -> float | None:
    """非有限值和缺失符号不能作为零参与指标。"""
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        text = str(value)[:10]
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").astimezone().date()
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _available(row: dict) -> date | None:
    notice = _date(row.get("NOTICE_DATE"))
    update = _date(row.get("UPDATE_DATE"))
    # 若接口提供更晚修订日期，当前版本数据只能在修订后使用。
    return max(notice, update) if notice and update else notice


def _annual_rows(rows: list[dict], symbol: str, as_of: date) -> dict[int, dict]:
    found: dict[int, dict] = {}
    for row in rows:
        report = _date(row.get("REPORT_DATE"))
        available = _available(row)
        if str(row.get("SECURITY_CODE", "")).zfill(6) != symbol or not report or not available:
            continue
        if (report.month, report.day) != (12, 31) or available > as_of or report >= as_of:
            continue
        if report.year < as_of.year - 4:
            continue
        current = found.get(report.year)
        if current is None or available >= (_available(current) or date.min):
            found[report.year] = row
    return found


def merge_annual_financials(symbol: str, as_of: date, cashflows: list[dict],
                            balances: list[dict], incomes: list[dict], dividends: list[dict],
                            quotes: dict[str, dict]) -> list[AnnualFinancialSnapshot]:
    """仅合并12月31日累计年报；分红含同年度中期及末期已实施派息。"""
    cf = _annual_rows(cashflows, symbol, as_of)
    bs = _annual_rows(balances, symbol, as_of)
    inc = _annual_rows(incomes, symbol, as_of)
    dividends_by_year: dict[int, list[dict]] = {}
    seen: set[tuple] = set()
    for row in dividends:
        report = _date(row.get("REPORT_DATE"))
        available = _available(row)
        ex_date = _date(row.get("EX_DIVIDEND_DATE"))
        if str(row.get("SECURITY_CODE", "")).zfill(6) != symbol or not report or not available:
            continue
        if available > as_of or not ex_date or ex_date > as_of or report.year < as_of.year - 4:
            continue
        if row.get("ASSIGN_PROGRESS") != "实施分配":
            continue
        bonus = finite_number(row.get("PRETAX_BONUS_RMB"))
        if bonus is None or bonus < 0:
            continue
        key = (report, ex_date, bonus)
        if key in seen:
            continue
        seen.add(key)
        dividends_by_year.setdefault(report.year, []).append(row)
    quote = quotes.get(symbol, {})
    quote_date = _date(quote.get("date"))
    valid_quote = bool(quote_date and quote_date <= as_of)
    records = []
    for year in sorted(set(cf) | set(bs) | set(inc) | set(dividends_by_year))[-4:]:
        if year >= as_of.year:
            continue
        c, b, i = cf.get(year, {}), bs.get(year, {}), inc.get(year, {})
        ds = dividends_by_year.get(year, [])
        known = [_available(r) for r in [c, b, i, *ds] if r]
        available_dates = [d for d in known if d is not None]
        if not available_dates:
            continue
        # 参考企业价值采用总负债减货币资金，与有息债务估算明确区分。
        debt = finite_number(b.get("TOTAL_LIABILITIES"))
        records.append(AnnualFinancialSnapshot(
            report_date=date(year, 12, 31), available_date=max(available_dates),
            operating_cash_flow=finite_number(c.get("NETCASH_OPERATE")),
            capital_expenditure=finite_number(c.get("CONSTRUCT_LONG_ASSET")),
            net_profit=finite_number(i.get("PARENT_NETPROFIT")),
            cash=finite_number(b.get("MONETARYFUNDS")), total_debt=debt,
            dividend_per_share=sum(float(d["PRETAX_BONUS_RMB"]) / 10 for d in ds) if ds else None,
            market_cap=finite_number(quote.get("market_cap")) if valid_quote else None,
            close_price=finite_number(quote.get("close")) if valid_quote else None,
            market_cap_date=quote_date if valid_quote else None,
        ))
    return records


def parse_price_rows(symbol: str, rows: list[dict], provider: str = "csi") -> list[IndexPriceData]:
    """统一日期升序去重，百分数保持公开源口径。"""
    by_date: dict[date, IndexPriceData] = {}
    for row in rows:
        day = _date(row.get("tradeDate", row.get("日期", row.get("date"))))
        close = finite_number(row.get("close", row.get("收盘", row.get("收盘价"))))
        if day is None or close is None or close <= 0:
            continue
        def value(en: str, cn: str, cn2: str, row: dict = row, close: float = close) -> float:
            number = finite_number(row.get(en, row.get(cn, row.get(cn2))))
            return number if number is not None and number >= 0 else close
        pct = finite_number(row.get("changePct", row.get("涨跌幅", row.get("change_pct"))))
        if provider == "cni" and pct is not None:
            pct *= 100
        volume = finite_number(row.get("tradingVol", row.get("volume", row.get("成交量"))))
        turnover = finite_number(row.get("tradingValue", row.get("成交额")))
        by_date[day] = IndexPriceData(
            symbol=symbol, trade_date=day, close=close,
            open=value("open", "开盘", "开盘价"), high=value("high", "最高", "最高价"),
            low=value("low", "最低", "最低价"), volume=int(max(volume or 0, 0)),
            change_pct=pct, turnover=turnover,
        )
    result = sorted(by_date.values(), key=lambda p: p.trade_date)
    for previous, current in pairwise(result):
        if current.change_pct is None:
            current.change_pct = (current.close / previous.close - 1) * 100
    return result


class StrategyDataProvider:
    """公开数据边界；HTTP均有超时，错误也短期缓存，避免持续打满上游。"""
    def __init__(self, cache_path: Path | None = None, timeout: float = 12, max_workers: int = 4):
        if cache_path is None:
            from utils.config import Config
            cache_path = Config().config_dir / "strategy_cache.db"
        self._cache = CacheManager(cache_path)
        self.timeout = timeout
        self.max_workers = min(max(max_workers, 1), 4)

    def _get_json(self, url: str, params: dict | None = None) -> dict:
        response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _read_cached(self, kind: str, key: str) -> Any:
        raw = self._cache.get(kind, key, "v2")
        return json.loads(raw) if raw is not None else None

    def _write_cached(self, kind: str, key: str, value: Any, ttl: int = 86400) -> None:
        self._cache.put(kind, key, "v2", json.dumps(value, ensure_ascii=False, default=str), ttl)

    def _official_prices(self, symbol: str, provider: str, end: date) -> list[IndexPriceData]:
        start = end - timedelta(days=400)
        if provider == "cni":
            data = self._get_json("https://hq.cnindex.com.cn/market/market/getIndexDailyDataWithDataFormat", {
                "indexCode": symbol, "startDate": start.isoformat(), "endDate": end.isoformat(), "frequency": "day",
            }).get("data", {})
            rows = []
            for values in data.get("data", []):
                rows.append({"date": values[0], "high": values[2], "open": values[3], "low": values[4], "close": values[5],
                             "changePct": finite_number(str(values[7]).replace("%", "")), "volume": values[9]})
            # 国证原始HTTP百分号已是百分数，区别于AkShare返回的小数。
            return parse_price_rows(symbol, rows, provider="csi")
        data = self._get_json("https://www.csindex.com.cn/csindex-home/perf/index-perf", {
            "indexCode": symbol, "startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"),
        })
        return parse_price_rows(symbol, data.get("data") or [])

    def _fallback_prices(self, symbol: str, provider: str, end: date) -> list[IndexPriceData]:
        # 独立的东财行情源；失败不会阻止官方源，日期范围一致。
        secid = f"0.{symbol}" if provider == "cni" else f"{'1' if symbol == '000015' else '2'}.{symbol}"
        return self._eastmoney_prices(symbol, secid, end, adjust="0")

    def _eastmoney_prices(self, symbol: str, secid: str, end: date, adjust: str) -> list[IndexPriceData]:
        data = self._get_json("https://push2his.eastmoney.com/api/qt/stock/kline/get", {
            "secid": secid, "klt": "101", "fqt": adjust, "beg": (end-timedelta(days=400)).strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"), "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }).get("data") or {}
        rows = []
        for line in data.get("klines", []):
            v = line.split(",")
            if len(v) >= 9:
                rows.append({"date": v[0], "open": v[1], "close": v[2], "high": v[3], "low": v[4], "volume": v[5], "changePct": v[8]})
        return parse_price_rows(symbol, rows)

    def fetch_prices(self, symbol: str, provider: str = "csi", end: date | None = None) -> list[IndexPriceData]:
        end = end or datetime.now().astimezone().date()
        key = f"{provider}:{symbol}:{end}"
        cached = self._read_cached("strategy_price", key)
        if cached is not None:
            return [IndexPriceData.model_validate(v) for v in cached]
        for source in (self._official_prices, self._fallback_prices):
            try:
                prices = source(symbol, provider, end)
                if prices:
                    self._write_cached("strategy_price", key, [v.model_dump(mode="json") for v in prices])
                    return prices
            except Exception as exc:  # noqa: BLE001 — 独立网络源失败逐个降级
                logger.warning("策略价格源 %s 失败 %s: %s", source.__name__, symbol, exc)
        self._write_cached("strategy_price", key, [], ttl=300)
        return []

    def fetch_etf_prices(self, symbol: str, end: date | None = None) -> list[IndexPriceData]:
        end = end or datetime.now().astimezone().date()
        key = f"{symbol}:qfq:{end}"
        cached = self._read_cached("strategy_etf_price", key)
        if cached is not None:
            return [IndexPriceData.model_validate(v) for v in cached]
        prices = []
        for source in (self._tencent_etf_prices, self._eastmoney_etf_prices):
            try:
                prices = source(symbol, end)
                if prices:
                    break
            except Exception as exc:  # noqa: BLE001 — 独立ETF源逐个降级
                logger.warning("ETF复权行情源 %s 失败 %s: %s", source.__name__, symbol, exc)
        if not prices:
            logger.warning("ETF所有前复权行情源不可用 %s", symbol)
        self._write_cached("strategy_etf_price", key, [v.model_dump(mode="json") for v in prices], ttl=86400 if prices else 300)
        return prices

    def _eastmoney_etf_prices(self, symbol: str, end: date) -> list[IndexPriceData]:
        secid = f"{'1' if symbol.startswith('5') else '0'}.{symbol}"
        return self._eastmoney_prices(symbol, secid, end, adjust="1")

    def _tencent_etf_prices(self, symbol: str, end: date) -> list[IndexPriceData]:
        code = f"{'sh' if symbol.startswith('5') else 'sz'}{symbol}"
        start = end - timedelta(days=400)
        data = self._get_json("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get", {
            "param": f"{code},day,{start.isoformat()},{end.isoformat()},640,qfq",
        }).get("data", {}).get(code, {})
        raw = data.get("qfqday")
        if not raw:
            raise ValueError("腾讯ETF前复权序列不可用；不能使用未复权day替代")
        rows = [{"date": v[0], "open": v[1], "close": v[2], "high": v[3], "low": v[4], "volume": v[5]} for v in raw if len(v) >= 6]
        return [p for p in parse_price_rows(symbol, rows) if start <= p.trade_date <= end]

    def _members(self, symbol: str, provider: str) -> tuple[list[ConstituentSnapshot], date]:
        key = f"{provider}:{symbol}"
        cached = self._read_cached("strategy_members", key)
        if cached is not None:
            return [ConstituentSnapshot.model_validate(v) for v in cached["members"]], date.fromisoformat(cached["as_of"])
        if provider == "cni":
            url = "https://www.cnindex.com.cn/sample-detail/download-history"
            response = requests.get(url, params={"indexcode": symbol}, timeout=self.timeout)
            columns = ["日期", "symbol", "name", "industry", "market_cap", "weight"]
        else:
            url = f"https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/closeweight/{symbol}closeweight.xls"
            response = requests.get(url, timeout=self.timeout)
            columns = ["日期", "index", "index_name", "index_en", "symbol", "name", "name_en", "exchange", "exchange_en", "weight"]
        response.raise_for_status()
        df: Any = pd.read_excel(BytesIO(response.content))
        if len(df.columns) != len(columns):
            raise ValueError("官方权重文件列结构变化")
        df.columns = columns
        classifier = IndustryClassifier()
        dates = [_date(v) for v in df["日期"]]
        known_dates = [v for v in dates if v is not None]
        if not known_dates:
            raise ValueError("官方权重缺少有效日期")
        as_of = max(known_dates)
        by_symbol = {}
        for row in df.to_dict("records"):
            if _date(row["日期"]) != as_of:
                continue
            weight = finite_number(row["weight"])
            code = str(row["symbol"]).split(".")[0].zfill(6)
            if weight is None or weight < 0 or weight > 100 or len(code) != 6 or not code.isdigit():
                continue
            classification = classifier.lookup(code)
            raw_industry = row.get("industry")
            industry = (str(raw_industry).strip() if pd.notna(raw_industry) else "") if provider == "cni" else classification.sw_level1 if classification.is_verified else ""
            by_symbol[code] = ConstituentSnapshot(symbol=code, name=str(row["name"]), weight=weight, industry=industry or None)
        members = list(by_symbol.values())
        self._write_cached("strategy_members", key, {"members": [v.model_dump(mode="json") for v in members], "as_of": as_of})
        return members, as_of

    def _cached_report(self, report: str, symbols: list[str], as_of: date) -> tuple[list[dict], list[str]]:
        digest = hashlib.sha256(",".join(sorted(symbols)).encode()).hexdigest()[:20]
        key = f"{report}:{digest}:{as_of}"
        cached = self._read_cached("strategy_financial", key)
        if cached is not None:
            return cached["rows"], cached["errors"]
        rows: list[dict] = []
        errors: list[str] = []
        codes = ",".join(f'"{s}"' for s in symbols)
        params = {"reportName": report, "columns": "ALL", "filter": f'(SECURITY_CODE in ({codes}))(REPORT_DATE>=\'{as_of.year - 4}-01-01\')',
                  "pageSize": "500", "pageNumber": "1", "sortColumns": "REPORT_DATE", "sortTypes": "-1"}
        try:
            for page in range(1, 21):
                params["pageNumber"] = str(page)
                response = self._get_json("https://datacenter-web.eastmoney.com/api/data/v1/get", params)
                result = response.get("result") or {}
                if not response.get("success", bool(result)):
                    raise ValueError(str(response.get("message", "公开财务接口失败")))
                rows.extend(result.get("data") or [])
                if page >= int(result.get("pages", 1)):
                    break
            else:
                errors.append("年度财务分页超出上限，样本可能不完整")
        except Exception as exc:  # noqa: BLE001 — 财务网络边界保留缺失原因
            logger.warning("公开财务采集失败 %s: %s", report, exc)
            labels = {"RPT_DMSK_FN_CASHFLOW": "年度现金流", "RPT_F10_FINANCE_GBALANCE": "年度资产负债", "RPT_DMSK_FN_INCOME": "年度利润", "RPT_SHAREBONUS_DET": "已实施分红"}
            errors.append(f"{labels.get(report, '公开财务')}数据不可用（{type(exc).__name__}），请稍后重试")
        self._write_cached("strategy_financial", key, {"rows": rows, "errors": errors}, ttl=86400 if rows else 300)
        return rows, errors

    def _tencent_quotes(self, symbols: list[str], as_of: date) -> dict[str, dict]:
        """腾讯公开行情：股价元，总市值亿元；二者同一时间戳。"""
        codes = ",".join(f"{'sh' if s.startswith('6') else 'bj' if s.startswith(('4', '8', '9')) else 'sz'}{s}" for s in symbols)
        response = requests.get(f"https://qt.gtimg.cn/q={codes}", timeout=self.timeout)
        response.raise_for_status()
        quotes = {}
        for line in response.text.split(";"):
            if '="' not in line:
                continue
            parts = line.split('="', 1)[1].rstrip('"').split("~")
            if len(parts) <= 45:
                continue
            sampled = _date(parts[30][:8])
            price, cap = finite_number(parts[3]), finite_number(parts[45])
            if sampled and sampled <= as_of and (as_of - sampled).days <= 14 and price is not None and price > 0 and cap is not None and cap > 0:
                quotes[parts[2]] = {"close": price, "market_cap": cap * 1e8, "date": sampled.isoformat()}
        return quotes

    def _eastmoney_quotes(self, symbols: list[str], as_of: date) -> dict[str, dict]:
        secids = ",".join(f"{'1' if s.startswith('6') else '0'}.{s}" for s in symbols)
        data = self._get_json("https://push2.eastmoney.com/api/qt/ulist.np/get", {"secids": secids, "fields": "f12,f2,f20,f124", "fltt": "2"}).get("data") or {}
        quotes = {}
        for row in data.get("diff", []):
            timestamp = finite_number(row.get("f124"))
            sampled = datetime.fromtimestamp(timestamp).astimezone().date() if timestamp else None
            if sampled and sampled <= as_of and (as_of - sampled).days <= 14:
                quotes[str(row["f12"])] = {"close": finite_number(row.get("f2")), "market_cap": finite_number(row.get("f20")), "date": sampled.isoformat()}
        return quotes

    def _quotes(self, symbols: list[str], as_of: date) -> tuple[dict[str, dict], list[str]]:
        key = hashlib.sha256((str(as_of)+",".join(sorted(symbols))).encode()).hexdigest()[:24]
        cached = self._read_cached("strategy_quotes", key)
        if cached is not None:
            return cached["quotes"], cached["errors"]
        quotes: dict[str, dict] = {}
        errors: list[str] = []
        for offset in range(0, len(symbols), 50):
            chunk = symbols[offset:offset + 50]
            failures = []
            for source in (self._tencent_quotes, self._eastmoney_quotes):
                try:
                    found = source([s for s in chunk if s not in quotes], as_of)
                    quotes.update(found)
                    if all(s in quotes for s in chunk):
                        break
                except Exception as exc:  # noqa: BLE001 — 独立同日报价源逐个降级
                    logger.warning("同日报价源 %s 失败: %s", source.__name__, exc)
                    failures.append(f"{source.__name__}: {type(exc).__name__}")
            missing = [s for s in chunk if s not in quotes]
            if missing:
                errors.append(f"{len(missing)}只成分股同日市值/价格缺失；{'、'.join(failures)}")
        self._write_cached("strategy_quotes", key, {"quotes": quotes, "errors": errors}, ttl=86400 if quotes else 300)
        return quotes, errors

    def collect(self, target: AnalysisTarget) -> StrategySnapshot:
        entry = IndexMapping().lookup(target.symbol)
        provider = entry.provider if entry and entry.provider else "csi"
        kind = entry.strategy_kind if entry else ""
        errors: list[str] = []
        today = datetime.now().astimezone().date()
        try:
            members, weight_as_of = self._members(target.symbol, provider)
            as_of = today
        except Exception as exc:  # noqa: BLE001 — 官方成分边界失败仍返回可审计快照
            logger.warning("策略成分权重失败 %s: %s", target.symbol, exc)
            return StrategySnapshot(source=provider, as_of=today, strategy_kind=kind or "", errors=["官方成分权重暂不可用，请稍后重试"])
        if not members:
            return StrategySnapshot(source=provider, as_of=as_of, strategy_kind=kind or "", errors=["官方成分权重为空"])
        if (today - weight_as_of).days > 7:
            errors.append(f"官方权重较旧：{weight_as_of}，使用最新公开权重而非当日成分")
        symbols = [m.symbol for m in members]
        reports = ["RPT_DMSK_FN_CASHFLOW", "RPT_F10_FINANCE_GBALANCE", "RPT_DMSK_FN_INCOME", "RPT_SHAREBONUS_DET"]
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [pool.submit(self._cached_report, report, symbols, as_of) for report in reports]
            datasets = []
            for future in futures:
                rows, failures = future.result()
                datasets.append(rows)
                errors.extend(failures)
        quotes, failures = self._quotes(symbols, as_of)
        errors.extend(failures)
        for member in members:
            member.annual_financials = merge_annual_financials(member.symbol, as_of, datasets[0], datasets[1], datasets[2], datasets[3], quotes)
        return StrategySnapshot(source=f"{provider}官方成分权重；东方财富公开年度财务及实施分红", as_of=as_of,
                                weight_as_of=weight_as_of, members=members, coverage={"member_weight_pct": sum(m.weight for m in members)},
                                errors=errors, strategy_kind=kind or "")
