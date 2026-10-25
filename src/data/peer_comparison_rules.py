"""同行比较纯规则：百分数原样保留，期间不匹配不参与统计。"""
from datetime import date, datetime
from typing import Any

from utils.numbers import is_finite_number, is_positive_finite

LABELS = {"pe_ttm": "PE TTM", "pb": "PB", "revenue_growth_yoy": "营收同比",
          "net_margin": "净利率", "roe": "ROE"}
BUSINESS_KEYS = ("revenue_growth_yoy", "net_margin", "roe")


def finite(value: Any) -> float | None:
    """拒绝布尔、占位和非有限值，不按绝对值猜测百分比单位。"""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if is_finite_number(number) else None


def iso_date(value: Any) -> str:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError):
        return ""


def valuation_date(value: Any) -> str:
    """估值日期必须已经发生，ISO 可解析不代表未来日期可以作为证据。"""
    period = iso_date(value)
    return period if period and period <= datetime.now().astimezone().date().isoformat() else ""


def metric_summary(key: str, target: Any, peers: list[dict[str, Any]],
                   target_symbol: str) -> dict[str, Any]:
    """全体去重有效同行的中位数和中秩分位，目标从分母排除。"""
    positive = key in ("pe_ttm", "pb")
    valid = is_positive_finite if positive else is_finite_number
    values = []
    seen = {target_symbol}
    for row in peers:
        symbol = row["symbol"]
        if symbol in seen:
            continue
        seen.add(symbol)
        value = finite(row.get(key))
        if (not row.get("reason") and not row.get("excluded_reasons", {}).get(key)
                and value is not None and valid(value)):
            values.append(value)
    number = finite(target)
    target_valid = number is not None and valid(number)
    enough = len(values) >= 5
    ordered = sorted(values)
    middle = (ordered[(len(ordered) - 1) // 2] / 2 + ordered[len(ordered) // 2] / 2) if enough else None
    percentile = None
    if enough and target_valid:
        percentile = 100 * (sum(v < number for v in values) + sum(v == number for v in values) / 2) / len(values)
    reason = "" if enough else f"有效同行仅 {len(values)} 家，少于 5 家"
    if not target_valid:
        reason += ("；" if reason else "") + ("目标倍数不适用或缺失" if positive else "目标数据缺失")
    return {"key": key, "label": LABELS[key], "unit": "multiple" if positive else "percent",
            "target": number, "median": middle, "percentile": percentile,
            "sample_count": len(values), "status": "ok" if enough and target_valid else "partial" if enough else "insufficient",
            "reason": reason}


def normalize_financial_rows(rows: list[dict[str, Any]], market: str) -> list[dict[str, Any]]:
    """只读取已验证的源字段，不用金额自行拼接净利率或混用年化 ROE。"""
    result = []
    hk = market == "hk"
    for row in rows:
        end = iso_date(row.get("REPORT_DATE"))
        symbol = str(row.get("SECUCODE" if hk else "SECURITY_CODE") or "")
        if not end or not symbol:
            continue
        if hk:
            start = iso_date(row.get("START_DATE"))
            kind = str(row.get("DATE_TYPE_CODE") or "")
            basis = "累计·平均权益ROE（未年化）"
            annual = bool(start and (date.fromisoformat(end) - date.fromisoformat(start)).days in (364, 365))
            valid_period = bool(start and start <= end and kind in ("001", "002", "003", "004"))
            if kind == "001" and not annual:
                valid_period = False
            growth, margin, roe = (row.get(k) for k in ("OPERATE_INCOME_YOY", "NET_PROFIT_RATIO", "ROE_AVG"))
        else:
            kind = str(row.get("REPORT_TYPE") or "")
            start = end[:4] + "-01-01"
            annual = kind == "年报" and end.endswith("-12-31")
            valid_period = end[5:] == {"年报": "12-31", "中报": "06-30", "一季报": "03-31", "三季报": "09-30"}.get(kind)
            basis = "累计·加权ROE"
            growth, margin, roe = (row.get(k) for k in ("TOTALOPERATEREVETZ", "XSJLL", "ROEJQ"))
        reason = "" if valid_period else "报告期间口径未确认"
        if end > datetime.now().astimezone().date().isoformat():
            reason = "报告截止日期在未来，不参与比较"
        result.append({"symbol": symbol, "name": str(row.get("SECURITY_NAME_ABBR") or ""),
                       "start": start, "end": end, "basis": basis, "report_type": kind,
                       "annual": annual, "period_basis": "cumulative", "reason": reason,
                       "revenue_growth_yoy": finite(growth), "net_margin": finite(margin), "roe": finite(roe)})
    unique = {}
    for row in result:
        key = tuple(row[k] for k in ("symbol", "start", "end", "basis", "report_type"))
        existing = unique.get(key)
        if existing is not None and any(existing.get(k) != row.get(k) for k in BUSINESS_KEYS):
            existing["reason"] = "同期间指标存在冲突版本，未参与统计"
        elif existing is None:
            unique[key] = row
    return list(unique.values())


def period_comparison(kind: str, rows: list[dict[str, Any]], target_symbol: str,
                      peer_symbols: list[str], *, expected_latest: str = "",
                      expected_start: str = "", expected_basis: str = "") -> dict[str, Any]:
    """以目标实际期间为锚；同截止日的不同财年、单季与累计不得混合。"""
    today = datetime.now().astimezone().date().isoformat()
    targets = [r for r in rows if r["symbol"] == target_symbol and not r.get("reason")
               and iso_date(r.get("end")) and r["end"] <= today
               and (kind != "annual" or r.get("annual"))]
    target = max(targets, key=lambda r: r["end"], default={})
    reason = ""
    if kind == "latest" and expected_latest and (not target or target["end"] < expected_latest):
        reason = f"未取得目标最新期 {expected_latest} 的同源经营指标；旧期仅供参考"
    if kind == "latest" and target and expected_start and target["end"] == expected_latest and target["start"] != expected_start:
        reason = "目标最新期起始日期与财务披露不一致，不参与比较"
    if kind == "latest" and target and expected_basis == "single":
        reason = "目标财务为单季、比较源为累计口径，不参与比较"
    if not target:
        reason = reason or "未取得目标同源经营指标"
    peers = []
    for symbol in dict.fromkeys(peer_symbols):
        if symbol == target_symbol:
            continue
        candidates = [r for r in rows if r["symbol"] == symbol]
        matches = [r for r in candidates if target and not r.get("reason") and r.get("end", "") <= today
                   and all(r.get(k) == target.get(k) for k in ("start", "end", "basis", "report_type"))]
        selected = matches or candidates
        row = dict(max(selected, key=lambda r: r["end"], default={"symbol": symbol}))
        if not matches:
            row["reason"] = "报告期间不一致" if candidates else "未取得数据"
        if reason:
            row["reason"] = reason
        peers.append(row)
    metrics = [metric_summary(k, None if reason else target.get(k), peers, target_symbol) for k in BUSINESS_KEYS]
    start, end = target.get("start", ""), target.get("end", "")
    report_type = target.get("report_type", "")
    report_label = {"001": "年报", "002": "中报", "003": "一季报", "004": "前三季度累计"}.get(report_type, report_type)
    if report_label == "三季报":
        report_label = "前三季度累计"
    label = "完整年度" if kind == "annual" else "最新一期" + ("·" + report_label if report_label else "")
    if kind == "latest" and reason:
        label = "旧期参考" + ("·" + report_label if report_label else "") if target else "报告期未取得"
    if start and end:
        label += f"（{start} 至 {end}）"
    return {"label": label, "start": start, "end": end,
            "basis": target.get("basis", ""), "source": "东财主要财务指标", "metrics": metrics,
            "peers": peers, "reason": reason}
