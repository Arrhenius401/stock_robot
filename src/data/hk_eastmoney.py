"""港股东财单标的请求；不扫描全市场，明确限时、限量与不复权口径。"""
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from numpy import bool_


def finite_number(value: Any) -> float | None:
    """上游空值、NaN 和无穷数均不作为财务或行情事实。"""
    if isinstance(value, (bool, bool_)):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


def financial_statement(code: str, statement: str) -> list[dict[str, Any]]:
    """直连同一东财长表接口；单次最多一万科目，拒收截断的历史。"""
    report_names = {"利润表": "RPT_HKF10_FN_INCOME_PC", "资产负债表": "RPT_HKF10_FN_BALANCE_PC",
                    "现金流量表": "RPT_HKF10_FN_CASHFLOW_PC"}
    date_column = "STD_REPORT_DATE" if statement == "资产负债表" else "START_DATE"
    response = requests.get("https://datacenter.eastmoney.com/securities/api/data/v1/get", params={
        "reportName": report_names[statement],
        "columns": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,ORG_CODE,REPORT_DATE,DATE_TYPE_CODE,"
                   f"FISCAL_YEAR,{date_column},STD_ITEM_CODE,STD_ITEM_NAME,AMOUNT",
        "quoteColumns": "", "filter": f'(SECUCODE="{code}.HK")', "pageNumber": "1", "pageSize": "10000",
        "sortTypes": "-1,1", "sortColumns": "REPORT_DATE,STD_ITEM_CODE", "source": "F10", "client": "PC",
    }, timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("东财港股财报响应格式异常或接口返回失败")
    result = payload.get("result")
    if not isinstance(result, dict) or "data" not in result:
        raise ValueError("东财港股财报结果缺失")
    records = result.get("data")
    count, pages = result.get("count"), result.get("pages")
    if (not isinstance(records, list) or type(count) is not int or type(pages) is not int
            or count < 0 or pages < 0):
        raise ValueError("东财港股财报明细或分页信息异常")
    if pages > 1 or count != len(records) or len(records) > 10000:
        raise ValueError("东财港股财报历史超过单次上限或分页不完整，拒绝截断数据")
    for row in records:
        if (not isinstance(row, dict) or row.get("SECUCODE") != f"{code}.HK"
                or row.get("SECURITY_CODE") != code):
            raise ValueError("东财港股财报标的代码不匹配或明细结构异常")
    return records


def financial_report_directory(code: str) -> list[dict[str, Any]]:
    """复用原版财报目录，单次获取当前标的的逐期币种与会计准则。"""
    response = requests.get("https://datacenter.eastmoney.com/securities/api/data/v1/get", params={
        "reportName": "RPT_CUSTOM_HKSK_APPFN_CASHFLOW_SUMMARY",
        "columns": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,START_DATE,REPORT_DATE,FISCAL_YEAR,"
                   "CURRENCY,ACCOUNT_STANDARD,REPORT_TYPE",
        "filter": f'(SECUCODE="{code}.HK")', "pageNumber": "1", "pageSize": "1",
        "source": "F10", "client": "PC",
    }, timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("东财港股财报目录响应失败或格式异常")
    result = payload.get("result")
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError("东财港股财报目录结果异常")
    records = data[0].get("REPORT_LIST")
    if not isinstance(records, list) or len(records) > 10000:
        raise ValueError("东财港股财报目录缺失或超过单次上限")
    for row in records:
        if (not isinstance(row, dict) or row.get("SECUCODE") != f"{code}.HK"
                or row.get("SECURITY_CODE") != code):
            raise ValueError("东财港股财报目录标的代码不匹配或结构异常")
    return records


def stock_snapshot(code: str) -> dict[str, Any]:
    response = requests.get("https://push2.eastmoney.com/api/qt/stock/get", params={
        "secid": f"116.{code}", "fields": "f43,f57,f58,f86,f164,f167", "fltt": "2", "invt": "2",
    }, timeout=10)
    response.raise_for_status()
    data = response.json().get("data")
    if not isinstance(data, dict) or str(data.get("f57", "")) != code:
        return {}
    return data


def valuation_comparison_snapshot(code: str) -> dict[str, Any]:
    """复用东财估值对比，仅请求目标自身一行，保留指标日期。"""
    response = requests.get("https://datacenter.eastmoney.com/securities/api/data/v1/get", params={
        "reportName": "RPT_PCF10_INDUSTRY_HKCVALUE",
        "columns": "SECUCODE,CORRE_SECUCODE,REPORT_DATE,PS_TTM",
        "filter": f'(SECUCODE="{code}.HK")(CORRE_SECUCODE="{code}.HK")',
        "pageNumber": "1", "pageSize": "1", "source": "F10", "client": "PC",
    }, timeout=10)
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result") if isinstance(payload, dict) and payload.get("success") is True else None
    if (not isinstance(result, dict) or type(result.get("count")) is not int or type(result.get("pages")) is not int
            or result.get("count") != 1 or result.get("pages") != 1
            or not isinstance(result.get("data"), list) or len(result["data"]) != 1):
        raise ValueError("东财港股估值对比结果缺失或分页异常")
    row = result["data"][0]
    if (not isinstance(row, dict) or row.get("SECUCODE") != f"{code}.HK"
            or row.get("CORRE_SECUCODE") != f"{code}.HK"):
        raise ValueError("东财港股估值对比代码不匹配")
    period = datetime.fromisoformat(str(row.get("REPORT_DATE"))).date()
    if period > datetime.now(timezone(timedelta(hours=8))).date():
        raise ValueError("东财港股估值对比日期晚于采集日期")
    return row


def daily_history(code: str, start: str, end: str, limit: int) -> list[str]:
    response = requests.get("https://push2his.eastmoney.com/api/qt/stock/kline/get", params={
        "secid": f"116.{code}", "klt": "101", "fqt": "0", "beg": start, "end": end,
        "lmt": str(limit), "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }, timeout=10)
    response.raise_for_status()
    data = response.json().get("data")
    if not isinstance(data, dict) or str(data.get("code", "")) != code:
        return []
    rows = data.get("klines")
    return rows if isinstance(rows, list) else []
