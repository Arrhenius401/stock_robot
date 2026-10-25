"""A 股独立备用财务包：本供应商内组合报表，不猜测跨源等效证据。"""

import logging
import math
import re
from datetime import date
from typing import Any

import requests
from bs4 import BeautifulSoup

from data.financial_selection import FinancialPackage
from data.schemas import FinancialData

logger = logging.getLogger(__name__)
_SINA_URL = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
_EM_BASE = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/"
_EM_MAIN = "https://datacenter.eastmoney.com/securities/api/data/get"


def _market(symbol: str) -> str:
    if re.fullmatch(r"[0-9]{6}", symbol) is None:
        raise ValueError("备用财务源只接受六位 A 股代码")
    if symbol.startswith("6"):
        return "sh"
    if symbol.startswith(("4", "8", "9")):
        return "bj"
    return "sz"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _choose(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        result = _number(row.get(key))
        if result is not None:
            return result
    return None


def _consistent(summary: dict[str, Any], table: dict[str, Any], pairs: tuple[tuple[str, str], ...]) -> bool:
    """仅拒绝已知重叠事实的矛盾；数值吻合不构成版本等效证据。"""
    for left, right in pairs:
        a, b = _number(summary.get(left)), _number(table.get(right))
        # 新浪每股数值有截断到六位小数的历史响应，允许末位一个单位差异。
        tolerance = 0.00000101 if "EPS" in left else 0.011
        if a is not None and b is not None and abs(a - b) > tolerance:
            logger.warning("同源财报重叠科目冲突：%s / %s，本期相关表不参与组合", left, right)
            return False
    return True


def _period(value: str) -> date:
    text = value[:10]
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    period = date.fromisoformat(text)
    if (period.month, period.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}:
        raise ValueError("备用财报日期不是标准报告期末")
    return period


def _json(url: str, params: dict[str, str]) -> dict[str, Any]:
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("备用财报响应不是对象")
    return payload


def _sina_table(symbol: str, table: str) -> dict[date, dict[str, Any]]:
    payload = _json(_SINA_URL, {
        "paperCode": _market(symbol) + symbol, "source": table,
        "type": "0", "page": "1", "num": "12",
    })
    result = payload["result"]
    if result["status"]["code"] != 0:
        raise ValueError("新浪财报接口状态异常")
    reports = result["data"]["report_list"]
    if not isinstance(reports, dict):
        raise TypeError("新浪财报列表不是对象")
    output: dict[date, dict[str, Any]] = {}
    for key, report in reports.items():
        if not isinstance(key, str) or not isinstance(report, dict):
            raise TypeError("新浪报告期或报表不是预期类型")
        period = _period(key)
        if report.get("rCurrency") != "CNY" or report.get("rType") != "合并期末":
            raise ValueError("新浪财报币种或合并口径不符")
        # 接口通常不回传代码；若回传则必须与请求一致，不能重新贴标签。
        for identity in ("symbol", "paperCode", "SECURITY_CODE"):
            if identity in report and report[identity] not in {symbol, _market(symbol) + symbol}:
                raise ValueError("新浪财报标的不符")
        row: dict[str, Any] = {}
        if not isinstance(report["data"], list):
            raise TypeError("新浪财报科目不是数组")
        for item in report["data"]:
            if not isinstance(item, dict):
                raise TypeError("新浪财报科目不是对象")
            field = item["item_field"]
            if not isinstance(field, str):
                raise TypeError("新浪财报科目代码不是字符串")
            value = _number(item.get("item_value"))
            if field in row and row[field] != value:
                raise ValueError("新浪财报重复科目冲突")
            row[field] = value
        if period in output and output[period] != row:
            raise ValueError("新浪财报重复报告期冲突")
        output[period] = row
    return output


def _em_rows(payload: dict[str, Any], symbol: str, *, main: bool = False) -> dict[date, dict[str, Any]]:
    if payload.get("success") is False:
        raise ValueError("东财财报接口状态异常")
    reports = payload["result"]["data"] if main else payload["data"]
    if not isinstance(reports, list):
        raise TypeError("东财财报列表不是数组")
    output: dict[date, dict[str, Any]] = {}
    for row in reports:
        if not isinstance(row, dict):
            raise TypeError("东财财报行不是对象")
        if row.get("SECUCODE") != f"{symbol}.{_market(symbol).upper()}":
            raise ValueError("东财财报标的不符")
        if row.get("SECURITY_CODE") not in {None, symbol} or row.get("CURRENCY") != "CNY":
            raise ValueError("东财财报代码或币种不符")
        # 三表请求 reportType=1 固定为合并报表；显式反证仍必须拒绝。
        if row.get("REPORT_SCOPE") not in {None, "合并"}:
            raise ValueError("东财财报不是合并口径")
        if not isinstance(row["REPORT_DATE"], str):
            raise TypeError("东财财报日期不是字符串")
        period = _period(row["REPORT_DATE"])
        if period in output and output[period] != row:
            raise ValueError("东财财报重复报告期冲突")
        output[period] = row
    return output


def _model(
    symbol: str, period: date, source: str, *, revenue: float | None,
    net_profit: float | None, deducted: float | None, assets: float | None,
    equity: float | None, ocf: float | None, ocfps: float | None,
    eps: float | None, roe: float | None, gross: float | None,
) -> FinancialData:
    start = date(period.year, 1, 1)
    row = FinancialData(
        symbol=symbol, fiscal_quarter=period, revenue=revenue, net_profit=net_profit,
        deducted_net_profit=deducted, total_assets=assets, total_equity=equity,
        operating_cash_flow=ocf, operating_cash_flow_per_share=ocfps, basic_eps=eps,
        roe=roe / 100 if roe is not None else None,
        gross_margin=gross / 100 if gross is not None else None,
        gross_margin_basis=f"{source}披露的销售毛利率（摘要口径，未重算）" if gross is not None else "",
        period_start=start, period_basis="cumulative", reporting_basis=f"{source}-合并累计报告期",
        report_currency="CNY", cash_flow_basis="reported" if ocf is not None else "unknown",
        cash_flow_period_start=start if ocf is not None else None,
    )
    row._data_source = source
    return row


def fetch_sina_financial(symbol: str) -> FinancialPackage:
    """独立获取新浪最近 12 期；单表失败不抹掉其余同源表。"""
    source = "新浪财报"
    tables: dict[str, dict[date, dict[str, Any]]] = {}
    try:
        _market(symbol)
    except ValueError:
        return FinancialPackage(source, [], {})
    for table in ("lrb", "fzb", "llb", "gjzb"):
        try:
            tables[table] = _sina_table(symbol, table)
        except (requests.RequestException, ValueError, TypeError, KeyError) as error:
            logger.warning("新浪财报 %s %s 不可用：%s", symbol, table, type(error).__name__)
            tables[table] = {}
    periods = sorted(set().union(*(set(table) for table in tables.values())))[-12:]
    rows = []
    for period in periods:
        income, balance, cash, main = (tables[key].get(period, {}) for key in ("lrb", "fzb", "llb", "gjzb"))
        # 银行营业收入不是普通制造业销售毛利率的适用分母。
        bank = "NETPARECOMPPROF" in income or "TOTSHAREQUI" in balance or "NETPARECOMPPROF" in main
        if not _consistent(main, income, (
            ("BIZTOTINCO", "BIZTOTINCO"), ("BIZINCO", "BIZINCO"),
            ("PARENETP", "PARENETP"), ("NETPARECOMPPROF", "NETPARECOMPPROF"), ("EPSBASIC", "BASICEPS"),
        )):
            income = {}
        if not _consistent(main, balance, (("TOTASSET", "TOTASSET"), ("RIGHAGGR", "RIGHAGGR"), ("TOTSHAREQUI", "TOTSHAREQUI"))):
            balance = {}
        if not _consistent(main, cash, (("MANANETR", "MANANETR"),)):
            cash = {}
        rows.append(_model(
            symbol, period, source, revenue=_choose(main, "BIZTOTINCO", "BIZINCO"),
            net_profit=_choose(income, "PARENETP", "NETPARECOMPPROF"),
            deducted=_choose(main, "NPCUT"), assets=_choose(balance, "TOTASSET"),
            equity=_choose(balance, "RIGHAGGR", "TOTSHAREQUI"), ocf=_choose(cash, "MANANETR"),
            ocfps=_choose(main, "OPNCFPS"), eps=_choose(main, "EPSBASIC"),
            roe=_choose(main, "ROEWEIGHTED"), gross=None if bank else _choose(main, "SGPMARGIN"),
        ))
    # 缺少可核对的准则、修订标识，不能据同名科目或数值相等开放跨源补位。
    return FinancialPackage(source, rows, {})


def fetch_eastmoney_financial(symbol: str) -> FinancialPackage:
    """独立获取东财最近 12 期；三表接口每批最多 5 个报告日。"""
    source = "东方财富财报"
    try:
        market = _market(symbol)
        main = _em_rows(_json(_EM_MAIN, {
            "type": "RPT_F10_FINANCE_MAINFINADATA", "sty": "APP_F10_MAINFINADATA",
            "filter": f'(SECUCODE="{symbol}.{market.upper()}")', "p": "1", "ps": "12",
            "sr": "-1", "st": "REPORT_DATE", "source": "HSF10", "client": "PC",
        }), symbol, main=True)
    except (requests.RequestException, ValueError, TypeError, KeyError) as error:
        logger.warning("东财财报 %s 指标不可用：%s", symbol, type(error).__name__)
        return FinancialPackage(source, [], {})
    if not main:
        return FinancialPackage(source, [], {})
    periods = sorted(main, reverse=True)[:12]
    company_type = None
    try:
        response = requests.get(_EM_BASE + "Index", params={"type": "web", "code": market + symbol}, timeout=10)
        response.raise_for_status()
        node = BeautifulSoup(response.text, "html.parser").find(id="hidctype")
        company_type = node.get("value") if node is not None else None
        if company_type not in {"1", "2", "3", "4"}:
            raise ValueError("东财公司报表类型未知")
    except (requests.RequestException, ValueError, TypeError) as error:
        logger.warning("东财财报 %s 报表类型不可用：%s", symbol, type(error).__name__)
        company_type = None
    tables: dict[str, dict[date, dict[str, Any]]] = {"balance": {}, "income": {}, "cash": {}}
    if company_type is not None:
        for label, route in (("balance", "zcfzbAjaxNew"), ("income", "lrbAjaxNew"), ("cash", "xjllbAjaxNew")):
            for offset in range(0, len(periods), 5):
                batch = periods[offset:offset + 5]
                try:
                    rows = _em_rows(_json(_EM_BASE + route, {
                        "companyType": str(company_type), "reportDateType": "0", "reportType": "1",
                        "dates": ",".join(period.isoformat() for period in batch), "code": (market + symbol).upper(),
                    }), symbol)
                    if not set(rows) <= set(batch):
                        raise ValueError("东财财报返回未请求的日期")
                    tables[label].update(rows)
                except (requests.RequestException, ValueError, TypeError, KeyError) as error:
                    logger.warning("东财财报 %s %s 批次 %s 不可用：%s", symbol, label, offset, type(error).__name__)
    output = []
    for period in sorted(periods):
        summary = main[period]
        balance, income, cash = (tables[key].get(period, {}) for key in ("balance", "income", "cash"))
        bank = company_type == "3" or summary.get("ORG_TYPE") == "银行"
        if not _consistent(summary, income, (
            ("PARENTNETPROFIT", "PARENT_NETPROFIT"), ("EPSJB", "BASIC_EPS"),
            ("KCFJCXSYJLR", "DEDUCT_PARENT_NETPROFIT"), ("TOTALOPERATEREVE", "TOTAL_OPERATE_INCOME"),
        )):
            income = {}
        output.append(_model(
            symbol, period, source, revenue=_choose(summary, "TOTALOPERATEREVE"),
            net_profit=_choose(summary, "PARENTNETPROFIT"), deducted=_choose(income, "DEDUCT_PARENT_NETPROFIT"),
            assets=_choose(balance, "TOTAL_ASSETS"), equity=_choose(balance, "TOTAL_EQUITY"),
            ocf=_choose(cash, "NETCASH_OPERATE"), ocfps=_choose(summary, "MGJYXJJE"),
            eps=_choose(summary, "EPSJB"), roe=_choose(summary, "ROEJQ"),
            gross=None if bank else _choose(summary, "XSMLL"),
        ))
    return FinancialPackage(source, output, {})
