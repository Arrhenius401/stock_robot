"""腾讯港股未复权日线及百度明确口径估值：每次请求十秒，无内部重试。"""
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from data.hk_eastmoney import finite_number


def tencent_history(code: str, start: str, end: str, limit: int) -> list[list[Any]]:
    """仅接受目标 day 序列；qfqday/hfqday 不能代替不复权行情。"""
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    response = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get", params={
        "param": f"hk{code},day,{start_date.isoformat()},{end_date.isoformat()},{limit},",
    }, timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise ValueError("腾讯港股日线响应异常")
    target = payload["data"].get(f"hk{code}")
    if not isinstance(target, dict):
        raise TypeError("腾讯港股代码不匹配或结果结构异常")
    if not isinstance(target.get("day"), list):
        raise TypeError("腾讯港股缺少不复权日线")
    return target["day"]


def baidu_metric(code: str, metric: str) -> dict[date, float]:
    """从指标自身曲线取日期，上市日期和采集日期不能代替指标日期。"""
    if metric not in ("市盈率(TTM)", "市净率"):
        raise ValueError("不支持的港股估值口径")
    response = requests.get("https://finance.baidu.com/opendata", params={
        "openapi": "1", "dspName": "iphone", "tn": "tangram", "client": "app", "query": metric,
        "code": code, "word": "", "resource_id": "51171", "market": "hk", "tag": metric,
        "chart_select": "近一年", "industry_select": "", "skip_industry": "1", "finClientType": "pc",
    }, timeout=10)
    response.raise_for_status()
    try:
        result = response.json()["Result"][0]["DisplayData"]["resultData"]["tplData"]["result"]
        if not isinstance(result, dict):
            raise TypeError("百度港股结果结构异常")
        if result.get("code") != code:
            raise ValueError("百度港股代码不匹配")
        charts = result["chartInfo"]
        if not isinstance(charts, list):
            raise TypeError("百度估值曲线结构异常")
        points: dict[date, float] = {}
        today = datetime.now(timezone(timedelta(hours=8))).date()
        for chart in charts:
            if not isinstance(chart, dict) or chart.get("header") != [metric]:
                continue
            body = chart.get("body")
            if not isinstance(body, list):
                continue
            for row in body:
                try:
                    if not isinstance(row, list) or len(row) != 2:
                        continue
                    period = date.fromisoformat(str(row[0]))
                    value = finite_number(row[1])
                    if period <= today and value is not None and value > 0:
                        points[period] = value
                except (ValueError, TypeError):
                    continue
        if not points:
            raise ValueError("百度估值缺少目标口径的合法日期指标")
        return points
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("百度估值响应结构异常") from error
