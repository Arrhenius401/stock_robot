"""按已核验离线证据研究分红支付率，不执行正式入池规则。"""

from datetime import date
from decimal import Decimal, DecimalException, localcontext
from typing import Any
from urllib.parse import urlsplit


def _amount(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
    except (DecimalException, ValueError):
        return None
    return result if result.is_finite() and abs(result.adjusted()) <= 1000 else None


def _sum_amounts(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    # 金额求和按输入位数扩展精度，避免默认上下文把小数分位舍去。
    with localcontext() as context:
        context.prec = max(50, max(value.adjusted() for value in values)
                           - min(int(value.as_tuple().exponent) for value in values)
                           + len(str(len(values))) + 2)
        return sum(values, Decimal(0))


def _evidence_valid(row: dict[str, Any], as_of: date, *, payment: bool = False) -> bool:
    source = row.get("source_url")
    if not isinstance(source, str):
        return False
    try:
        parsed = urlsplit(source)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            return False
        fields = ("disclosed_on", "paid_on") if payment else ("disclosed_on",)
        for field in fields:
            value = row.get(field)
            if not isinstance(value, str):
                return False
            observed = date.fromisoformat(value)
            year = row["fiscal_year"] if payment else row["year"]
            earliest = date(year, 1, 1) if payment else date(year + 1, 1, 1)
            if observed > as_of or observed < earliest:
                return False
    except ValueError:
        return False
    return True


def evaluate_company(record: dict[str, Any], as_of: date) -> dict[str, Any]:
    """固定取此前三个完整财政年度，证据不足返回待核验而非失败。"""
    years = list(range(as_of.year - 3, as_of.year))
    reasons: list[str] = []
    evidence: dict[str, list[dict[str, Any]]] = {"profits": [], "coverage": [], "events": []}
    result: dict[str, Any] = {
        "symbol": record.get("symbol"), "name": record.get("name"),
        "status": "pending_verification", "annual": [], "annual_average": None,
        "cumulative_ratio": None, "meets_reference_condition": None,
        "rule_enabled": False, "reasons": reasons, "evidence": evidence,
    }
    if any(not isinstance(record.get(key), str) or not record[key].strip()
           for key in ("symbol", "name")):
        reasons.append("股票代码或名称缺失")
    required = record.get("required_share_classes")
    if (not isinstance(required, list) or not required
            or any(item not in ("A", "H") for item in required)
            or len(set(required)) != len(required)):
        reasons.append("必须明确无重复的A股或A/H股覆盖范围")
        required = []

    # 按财政年度核对利润和完整性证据；不把报告披露年份当作财政年度。
    profit_values: dict[int, Decimal] = {}
    for field in ("profits", "coverage"):
        rows = record.get(field)
        if not isinstance(rows, list):
            reasons.append(f"{field}证据列表缺失或格式错误")
            continue
        seen: dict[int, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                reasons.append(f"{field}存在格式错误的证据行")
                continue
            year = row.get("year")
            if isinstance(year, bool) or not isinstance(year, int):
                reasons.append(f"{field}财政年度无效")
                continue
            if year not in years:
                if year >= as_of.year:
                    reasons.append(f"{field}包含尚未完成的财政年度{year}")
                continue
            if year in seen:
                if seen[year] != row:
                    reasons.append(f"{year}年{field}重复证据冲突")
                continue
            seen[year] = row
            evidence[field].append(dict(row))
            if not _evidence_valid(row, as_of):
                reasons.append(f"{year}年{field}来源或披露日期未核实")
            if field == "coverage":
                if row.get("complete") is not True:
                    reasons.append(f"{year}年分红事件完整性未核实")
            else:
                value = _amount(row.get("amount_yuan"))
                if value is None or value <= 0:
                    reasons.append(f"{year}年普通股归母利润必须是有限正金额")
                elif row.get("currency") != "CNY" or row.get("scope") != "company_ordinary":
                    reasons.append(f"{year}年利润币种或普通股股东口径未核实")
                else:
                    profit_values[year] = value
        for year in years:
            if year not in seen:
                reasons.append(f"缺少{year}年{field}证据")

    # 事件标识在公司内唯一，按股类分行；ALL与分股类并存不猜测覆盖关系。
    groups: dict[str, dict[str, dict[str, Any]]] = {}
    event_values: dict[tuple[str, str], Decimal] = {}
    rows = record.get("events")
    if not isinstance(rows, list):
        reasons.append("分红事件列表缺失或格式错误")
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            reasons.append("分红事件存在格式错误的行")
            continue
        event_id, share_class = row.get("event_id"), row.get("share_class")
        year = row.get("fiscal_year")
        if not isinstance(event_id, str) or not event_id.strip():
            reasons.append("分红事件标识缺失")
            continue
        if share_class not in ("A", "H", "ALL"):
            reasons.append(f"事件{event_id}股类未核实")
            continue
        if isinstance(year, bool) or not isinstance(year, int):
            reasons.append(f"事件{event_id}财政年度无效")
            continue
        if year >= as_of.year:
            reasons.append(f"事件{event_id}属于尚未完成的财政年度")
            continue
        group = groups.setdefault(event_id, {})
        if share_class in group:
            if group[share_class] != row:
                reasons.append(f"事件{event_id}/{share_class}重复标识存在冲突")
            continue
        group[share_class] = row
        if year not in years:
            continue
        evidence["events"].append(dict(row))
        if not _evidence_valid(row, as_of, payment=True):
            reasons.append(f"事件{event_id}/{share_class}来源、披露或实际派息日期未核实")
        value = _amount(row.get("amount_yuan"))
        if value is None or value < 0 or row.get("currency") != "CNY":
            reasons.append(f"事件{event_id}/{share_class}人民币实际派息金额无效")
        else:
            event_values[(event_id, share_class)] = value

    annual_values: dict[int, list[Decimal]] = {year: [] for year in years}
    year_events: dict[int, set[str]] = {year: set() for year in years}
    for event_id, group in groups.items():
        for row in group.values():
            if row["fiscal_year"] in years:
                year_events[row["fiscal_year"]].add(event_id)
    for event_id, group in groups.items():
        for row in group.values():
            year = row["fiscal_year"]
            if year not in years:
                continue
            scope = row.get("amount_scope", "event")
            if scope not in ("event", "fiscal_year_total"):
                reasons.append(f"事件{event_id}金额覆盖口径无效")
            if scope == "fiscal_year_total" and len(year_events[year]) > 1:
                reasons.append(f"{year}年全年分红合计与其他事件并存，须核实去重")
            covered = row.get("covered_event_ids", [])
            if (not isinstance(covered, list)
                    or any(not isinstance(item, str) or not item.strip() for item in covered)):
                reasons.append(f"事件{event_id}被覆盖事件标识无效")
            elif any(item in groups for item in covered):
                reasons.append(f"事件{event_id}与其覆盖的事件重复，须核实去重")
    for event_id, group in groups.items():
        first_row = next(iter(group.values()))
        if any(row.get("amount_scope", "event") != first_row.get("amount_scope", "event")
               or row.get("covered_event_ids", []) != first_row.get("covered_event_ids", [])
               for row in group.values()):
            reasons.append(f"事件{event_id}不同股类的金额覆盖口径冲突")
            continue
        event_years = {row["fiscal_year"] for row in group.values()}
        if len(event_years) != 1:
            reasons.append(f"事件{event_id}不同股类的财政年度冲突")
            continue
        year = next(iter(event_years))
        if year not in years:
            continue
        if "ALL" in group and len(group) > 1:
            reasons.append(f"事件{event_id}公司总额与分股类金额并存，须核实去重")
            continue
        if "ALL" not in group and set(group) != set(required):
            reasons.append(f"事件{event_id}未完整且唯一覆盖要求的股类")
            continue
        for share_class in group:
            value = event_values.get((event_id, share_class))
            if value is not None:
                annual_values[year].append(value)

    if reasons:
        return result
    try:
        dividends = {year: _sum_amounts(values) for year, values in annual_values.items()}
        # 高精度局部上下文避免金额经浮点换算；比率保留十进制字符串。
        with localcontext() as context:
            context.prec = 50
            ratios = [dividends[year] / profit_values[year] for year in years]
            average = sum(ratios, Decimal(0)) / Decimal(3)
            cumulative = _sum_amounts(list(dividends.values())) / _sum_amounts(list(profit_values.values()))
            result.update(
                status="complete",
                annual=[{"year": year, "dividend_yuan": str(dividends[year]),
                         "profit_yuan": str(profit_values[year]), "payout_ratio": str(ratio)}
                        for year, ratio in zip(years, ratios, strict=True)],
                annual_average=str(average), cumulative_ratio=str(cumulative),
                meets_reference_condition=0 < average < 1 and 0 < ratios[-1] < 1,
            )
    except DecimalException:
        reasons.append("金额精度或数量级超出可计算范围，须核实原始证据")
    return result
