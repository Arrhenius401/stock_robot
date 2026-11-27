"""数据表展示副本和精简短注，不改变原始分析与评分证据。"""
import re
from typing import Any

from report.formatter import (
    METRIC_DISPLAY_NAMES,
    format_metric_value,
    format_metrics,
    metric_display_name,
)

_FINANCIAL_DETAILS = {
    "revenue_period", "net_profit_period",
    "roe_basis", "amount_basis", "amount_currency_reason", "gross_margin_basis", "net_margin_basis",
    "dividend_yield_basis", "cash_flow_source", "cash_flow_period", "cash_flow_limitation",
    "yoy_basis", "trend_note", "annual_roe", "scoring_basis", "roe_trend", "operating_trends",
    "revenue_basis", "reported_revenue_basis", "reported_revenue_source",
    "reported_revenue_period",
}


def number_table_notes(values: dict[str, Any], groups: dict[tuple[str, ...], list[str]]) -> dict[str, Any]:
    """按表格首次引用顺序生成连续角标；相同说明跨字段复用编号。"""
    entries: dict[str, set[str]] = {}
    for labels, bodies in groups.items():
        body = "；".join(dict.fromkeys(readable_note(text).rstrip("。；") for text in bodies if text.strip()))
        if body:
            entries.setdefault(body + "。", set()).update(labels)
    superscripts = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
    assigned: dict[str, int] = {}
    shown = {}
    for label, value in values.items():
        markers = []
        for body, labels in entries.items():
            if label in labels:
                if body not in assigned:
                    assigned[body] = len(assigned) + 1
                markers.append(assigned[body])
        shown[label + "、".join(str(number).translate(superscripts) for number in sorted(markers))] = value
    return {"metrics": shown, "notes": [f"{str(number).translate(superscripts)} {body}" for body, number in assigned.items()]}


def stock_table_view(dimension: str, metrics: dict[str, Any],
                     display: dict[str, Any] | None = None, *,
                     currency_conversion: dict[str, Any] | None = None) -> dict[str, Any]:
    """Markdown 与结构化报告共用同一份展示副本，原始指标保持不变。"""
    if dimension == "valuation" and display:
        values = {**display["metrics"], "valuation_notes": metrics.get("valuation_notes", [])}
        return prepare_table(dimension, values, details=display.get("details", []), cutoff=display.get("cutoff", ""))
    if dimension == "financial" and currency_conversion:
        period = str(metrics.get("report_period") or metrics.get("latest_quarter") or "")[:10]
        for evidence in currency_conversion.get("financial", []):
            source = evidence.get("source") or {}
            currency = evidence.get("amount_currency_override")
            if (period and str(source.get("fiscal_quarter")) == period and currency
                    and "官方核验" in str(evidence.get("currency_note", ""))):
                original = re.search(r"CURRENCY=([A-Z]+)", str(source.get("amount_currency_reason", "")))
                original_currency = original[1] if original else source.get("report_currency")
                difference = f"；原接口曾标为{original_currency}" if original_currency and original_currency != currency else ""
                metrics = {**metrics, "_verified_currency_note": f"金额按已有核验记录采用{currency}{difference}"}
                break
    return prepare_table(dimension, metrics)


def readable_note(text: str) -> str:
    """仅整理展示文案，原始接口名、处理过程和完整说明保留在结构化证据中。"""
    text = str(text).strip()
    text = re.sub(r"币种冲突：Eastmoney 指标 CURRENCY=([A-Z]+)，目录优先", r"原接口标为\1，本次按财报目录的币种标注处理", text)
    text = re.sub(r"disclosure_currency=([A-Z]+)", r"列报币种为\1", text)
    replacements = {
        "；源科目映射/已披露现金支出派生": "",
        "源科目映射/已披露现金支出派生": "按财报项目计算",
        "东财美股 F10 行业（BELONG_INDUSTRY）": "东方财富美股行业分类",
        "（PB_SOURCE）": "",
        "累计·加权ROE": "年初累计数据；公司披露的加权净资产收益率",
        "累计·平均权益ROE（未年化）": "年初累计数据；按平均股东权益计算净资产收益率，未年化",
        "分位表示数值位置，不直接代表经营优劣": "分位表示数值在可比公司中的位置，不代表经营优劣",
        "排名含本公司；部分同行缺少同日有效市值时按有效样本排名": "排名包含本公司，仅比较取得同日市值的可比公司",
        "各项独立筛选，至少 5 家有效同行；目标公司不计入统计": "每项指标至少需5家可比公司；本公司不计入同行统计",
        "成员资格为港股通且已有恒生 HSICS 二级映射": "同行限于港股通内、恒生行业二级分类相同的公司",
        "分类快照陈旧，成分可能已变更": "行业名单较旧，成员可能已变化",
        "分类快照：": "行业名单日期：",
        "行业评分仅使用连续单季派生的 TTM": "同行经营指标采用最近12个月数据",
        "经营指标采用F10连续单季TTM": "同行经营指标采用最近12个月数据",
        "美股上市代码（含类别股）": "不同类别股分别统计",
        "优先目标市值0.1至5倍，不足五家放宽0.01至20倍，最多十家": "先选市值为本公司10%至5倍的同行；不足5家时扩大至1%至20倍，最多10家",
        "ROE采用四季归母净利与同期普通权益，PB仅作估值比较": "ROE按四季归母净利与同期普通权益计算；PB仅比较估值",
        "完整年度仅参考，不计分": "完整年度数据仅供参考，不计分",
        "至少两项指标各五家的年度同期样本不足": "完整年度数据不足，未满足至少两项指标各有5家可比公司的要求",
        "；FY": "；完整财年",
        "TTM最新期：": "最近12个月：",
        "；TTM": "；最近12个月合计",
        "所有财报公告/修订及实施日期不晚于采样日；权重日期、财报年度、市值日期独立展示": "仅使用采样日已公开的数据；各项日期另列",
        "股息率采用同年度中期及末期已实施每股派息/采样股价，非TTM": "股息率按同一年度已实施的中期及末期派息÷采样股价计算，不按最近12个月统计",
        "同花顺源披露销售净利率": "净利率由同花顺提供",
        "市值与东财TTM营收派生": "市值÷东方财富最近12个月营收",
        "TTM 最近四个财季截至": "四季营收截至",
        "；TTM 最近四个财季截至": "；四季营收截至",
        "；TTM最新期": "；最近12个月",
        "（期间平均汇率折算后派生，仅展示）": "（按期间平均汇率折算，仅展示）",
        "常规现金流为最近12个月合计，另列年度值不属此期间；自由现金流已扣资本开支": "现金流通常为最近12个月合计；另列年度值单独比较；自由现金流已扣资本开支",
        "最新期间资本开支不完整，展示最近完整年度，非 TTM": "近12个月资本开支不完整，年度值另列",
        "当前快照": "当前值",
        " 快照：": " 当前值：",
        "东方财富美股 财务数据 主指标": "东方财富美股财务指标",
        "历史估值由本次财务分母派生，仅参考": "历史估值按本次财报数据计算，仅供参考",
        "毛利率为派生值，分母为营运收入，可能不同于表内营业额": "毛利率按毛利÷营运收入计算，与表内营业额可能不是同一口径",
        "计算值：单季净利润÷营业收入；仅展示": "净利率=单季净利润÷营业收入，仅展示",
        "计算值：净利润÷营业收入（报告期口径）；仅展示": "净利率=同一财报期间的净利润÷营业收入，仅展示",
        "低于80%称已覆盖样本估算，缺失不填零": "数据齐全的成分股权重不足80%时，仅作样本估算；缺失不填零",
        "指标按覆盖样本权重加权平均": "仅用数据齐全的成分股按权重计算",
        "不构成官方因子复现或历史回测": "不代表官方计算结果，也不是历史回测",
        "官方权重较旧：": "成分权重日期较旧：",
        "使用最新公开权重而非当日成分": "采用最新公开权重，可能与当日成分不同",
        "公开官方数据未提供可验证的 PB 日频历史，PB 历史分位不可用": "官方未提供可核验的逐日市净率历史，无法计算PB历史分位",
        "ADX14与ATR20采用Wilder平滑；ADX不表示方向": "ADX衡量趋势强度，ATR衡量波动幅度；均采用Wilder平滑，ADX不表示涨跌方向",
        "东财F10": "东方财富财务数据",
        "东财主要财务指标": "东方财富财务指标",
        "源币种": "原始币种",
        "仅披露，不计入评分": "仅展示，不计分",
        "仅作展示，不参与评分": "仅展示，不计分",
        "日频": "逐日",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"RPT_USF10_[A-Z_]+", "东方财富美股财务指标", text)
    text = re.sub(r"(\d{4}-\d{2}-\d{2})T[\d:.]+(?:[+-]\d{2}:\d{2}|Z)?", r"\1", text)
    text = re.sub(r"长窗交易日历：([^，；]+)，\d{4}-\d{2}-\d{2} 至 \d{4}-\d{2}-\d{2}，\d+ 个日期", r"交易日据\1确认", text)
    text = re.sub(r"(?<![A-Z])(CNY|HKD|USD)(?![A-Z])", lambda match: {"CNY": "人民币", "HKD": "港元", "USD": "美元"}[match[1]], text)
    text = text.replace("非 TTM", "非最近12个月合计").replace("非TTM", "非最近12个月合计").replace("TTM", "最近12个月")
    text = text.replace("最近12个月合计 最近四个财季截至", "四季营收截至")
    text = text.replace("东财", "东方财富").replace("F10", "财务数据").replace("HKEX", "香港交易所")
    text = re.sub(r"\s*(人民币|港元|美元)\s*", r"\1", text)
    text = re.sub(r"https://(?:[\w-]+\.)?csindex\.com\.cn/[^\s、；，。]+", "中证指数官网", text)
    text = re.sub(r"中证指数官网(?:、中证指数官网)+", "中证指数官网", text)
    return "；".join(dict.fromkeys(text.split("；")))


def compact_valuation_notes(details: list[str]) -> list[str]:
    """删除重复统计展开；保留缺失、过期、派生等影响判断的信息。"""
    output = []
    samples: dict[int, list[str]] = {}
    for item in details:
        text = str(item).strip()
        if not text:
            continue
        if re.match(r"^(PE|PB).*历史[:：]", text) and "中位数" in text and "有效样本" in text:
            continue
        match = re.search(r"(PE|PB).*有效(?:日期)?样本\s*(\d+).*不足\s*(\d+)", text)
        if match:
            samples.setdefault(int(match[2]), []).append(match[1])
            continue
        if re.sub(r"\s+", "", text).startswith(("PE数据日期", "PB数据日期", "PE/PB当前值", "PS(TTM)：暂无数据")):
            continue
        if text.startswith("PS数据日期：") and any(
                note.startswith("PS(TTM)") and text.split("：", 1)[1] in note for note in output):
            continue
        if "历史分位不等于低风险" in text or "历史低分位不等于低风险" in text:
            continue
        if text.startswith("历史分位使用下述曲线参照值"):
            continue
        if text.startswith("PS(TTM)"):
            previous_index = next((index for index, note in enumerate(output) if note.startswith("PS(TTM)：")), None)
            previous = output[previous_index] if previous_index is not None else ""
            source_names = {source.replace("东财", "东方财富") for source in re.findall("新浪|东财|东方财富|百度|腾讯", text)}
            previous_sources = {source.replace("东财", "东方财富") for source in re.findall("新浪|东财|东方财富|百度|腾讯", previous)}
            if (previous and text.startswith("PS(TTM)：") and source_names == previous_sources
                    and set(re.findall(r"\d{4}-\d{2}-\d{2}", text)) <= set(re.findall(r"\d{4}-\d{2}-\d{2}", previous))
                    and not any(word in text for word in ("失败", "冲突", "缺失", "未核实"))):
                continue
            if previous_index is not None and text.startswith("PS(TTM) 派生：总市值 ÷ TTM 营收 ="):
                # 表内已有 PS 值；只补前面未出现的期间信息，不再次展开同一公式。
                for clause in text.split("；")[1:]:
                    if (clause.strip() and clause.strip() not in {"仅展示", "仅披露"}
                            and not any(day in previous for day in re.findall(r"\d{4}-\d{2}-\d{2}", clause))):
                        output[previous_index] += "；" + clause.strip()
                        previous = output[previous_index]
                continue
        # 分号属于同一条证据，拆开会丢失指标归属并造成大量游离段落。
        if text not in output:
            output.append(text)
    for count, labels in samples.items():
        output.append(f"{'、'.join(dict.fromkeys(labels))}历史样本 {count}／120 个，分位暂缺。")
    return list(dict.fromkeys(output))


def prepare_table(dimension: str, metrics: dict[str, Any], *,
                  details: list[str] | None = None, cutoff: str = "") -> dict[str, Any]:
    """统一数值表与短注；格式化后再加角标，避免比例单位丢失。"""
    shown = {}
    notes = []
    for key, value in format_metrics(metrics).items():
        if (dimension == "financial" and key in _FINANCIAL_DETAILS
                or isinstance(value, (list, dict))):
            continue
        if key in {"peer_names", "peer_display_note", "tag"}:
            continue
        if key in {"valuation_applicability", "historical_valuation_applicability"}:
            continue
        # 新增元数据必须显式声明展示名称，不能自动泄漏内部英文键。
        if key not in METRIC_DISPLAY_NAMES:
            continue
        label = metric_display_name(key)
        shown[label] = (str(value).split("；")[0] + "；展示 CNY" if key == "cash_flow_latest_period" and "展示 CNY" in str(value)
                        else format_metric_value(key, value))
    if dimension == "financial":
        if metrics.get("revenue_period"):
            notes.append(f"营业收入、净利润为单季数据（截至{metrics['revenue_period']}）。")
        if metrics.get("common_equity") is not None:
            notes.append("普通权益不含少数股东权益，用于市净率和净资产收益率计算。")
        revenue_basis = str(metrics.get("revenue_basis", ""))
        if metrics.get("revenue_period") and "单季" in revenue_basis:
            pass  # 单季期间已在前一条说明，不重复展开同一口径。
        elif "营业额" in revenue_basis:
            notes.append("表内营业收入为源披露营业额口径；不与其他口径混算。")
        elif revenue_basis:
            notes.append(f"营业收入为源披露{revenue_basis}口径；不与其他口径混算。")
        if metrics.get("dividend_yield") is not None:
            basis = str(metrics.get("dividend_yield_basis") or "")
            explanation = ("股息率来自已实施分红方案，未确认包含全年全部派息" if "方案" in basis
                           else "股息率为来源提供的最近12个月数值" if "TTM" in basis and not any(word in basis for word in ("未声明", "未说明", "不明"))
                           else "股息率沿用来源值，未说明是否覆盖最近12个月")
            notes.append(explanation + (f"；来源：{basis.split('；')[0]}" if basis else "") + "。")
        if "roe" in metrics:
            notes.append("ROE按最近12个月归母净利润÷期末归母股东权益计算，可能与公司披露值不同。"
                         if "TTM" in str(metrics.get("roe_basis", ""))
                         else "ROE沿用本次财报披露口径。")
        if "gross_margin" in metrics or "net_margin" in metrics:
            if not metrics.get("net_margin_basis"):
                notes.append("利润率对应表内财报期间，缺失不估算。")
            if metrics.get("net_margin_basis"):
                notes.append(str(metrics["net_margin_basis"]))
            gross = str(metrics.get("gross_margin_basis") or "")
            if "营运收入" in gross:
                notes.append("毛利率为派生值，分母为营运收入，可能不同于表内营业额。")
            elif "毛利/" in gross or "毛利／" in gross or "派生" in gross:
                notes.append("毛利率由毛利／收入计算，非直接披露。")
        if any(key in metrics for key in ("operating_cash_flow", "capital_expenditure", "free_cash_flow")):
            notes.append("常规现金流为最近12个月合计，另列年度值不属此期间；自由现金流已扣资本开支。"
                         if metrics.get("cash_flow_latest_period")
                         else "现金流为最近12个月合计；自由现金流已扣资本开支。")
            if "推算" in str(metrics.get("cash_flow_source", "")):
                notes.append("现金流为推算值，非直接披露。")
            elif "证据不足" in str(metrics.get("cash_flow_source", "")):
                notes.append("现金流期间或币种证据不足。")
            limitation = str(metrics.get("cash_flow_limitation") or "")
            if limitation:
                notes.append(limitation)
        if metrics.get("yoy_basis"):
            text = str(metrics["yoy_basis"])
            methods = [method for method in ("源披露", "精确金额复算") if method in text]
            notes.append("同比" + ("沿用来源披露值" if methods == ["源披露"] else "由可比金额计算" if methods == ["精确金额复算"]
                                   else "采用来源披露值或由可比金额计算" if methods else "采用已核验数据") + "。")
        amount = str(metrics.get("amount_basis", ""))
        verified = str(metrics.get("_verified_currency_note") or "")
        if verified:
            notes.append(verified + "。")
        elif amount:
            currency = re.search(r"(?<![A-Z])(CNY|HKD|USD|EUR|JPY|GBP)(?![A-Z])", amount)
            if "展示 CNY" in amount or "汇率未核实" in amount:
                notes.append(_currency_note(amount))
            elif "未核实" in amount or "未记录" in amount:
                notes.append("金额币种未核实，不能仅凭财报目录判断。")
            elif currency and ("利润表" in amount or "不代表其他报表" in amount):
                notes.append(f"利润表金额为{currency[1]}；不能据此判断其他报表币种。")
            else:
                notes.append(f"金额币种：{currency[1]}。" if currency else "金额沿用源币种。")
        reason = str(metrics.get("amount_currency_reason") or "")
        if reason:
            if verified:
                reason = "；".join(part for part in reason.split("；") if "目录优先" not in part
                                  and any(word in part for word in ("失败", "缺失", "未核实", "未核验", "证据不足", "不可证明")))
            if reason:
                notes.append(_currency_note(reason))
        limitation = str(metrics.get("trend_note") or "")
        if limitation:
            if "精度" in limitation or "修订" in limitation:
                missing = [metric_display_name(key) for key in metrics if key.endswith("growth_yoy") and metrics[key] is None]
                if missing:
                    notes.append("、".join(missing) + "未取得可核验的可比数据，暂不展示。")
            elif "不足" in limitation or "冲突" in limitation:
                notes.append("部分同期样本不足或冲突，不作趋势判断。")
    elif dimension == "technical" and "volume_ratio" in metrics:
        notes.append("前复权；量比比较近 5 日与此前 20 日均量。")
    elif dimension == "valuation":
        notes = compact_valuation_notes([
            *(details or []), *metrics.get("valuation_details", []),
            *metrics.get("valuation_notes", []), *valuation_window_notes(cutoff),
        ])
        if cutoff:
            notes.insert(0, cutoff.split("；")[0])
        if "target_pe_premium" in metrics:
            notes.append("相对行业PE溢价以可比公司的PE中位数计算，不直接代表高估程度。")
    if metrics.get("reported_revenue_source"):
        notes.append(f"SEC 营业收入：{metrics.get('reported_revenue_period') or '期间未记录'}；"
                     f"{metrics['reported_revenue_source']}；仅展示。")
    return _linked_notes(dimension, shown, notes)


def _currency_note(text: str) -> str:
    """正文保留币种风险；逐笔折算审计信息继续留在结构化证据中。"""
    if "展示 CNY" in text and "汇率未核实" not in text:
        risks = [part.strip() for part in text.split("；") if any(word in part for word in (
            "冲突", "未核实", "未核验", "失败", "缺失", "不可用", "不一致", "证据不足"))]
        return "；".join(dict.fromkeys(["金额展示为 CNY", *risks])) + "。"
    return text


def _note_fields(text: str, dimension: str) -> list[str]:
    """将说明绑定到指标组；未知限制也必须有表内入口，不能静默丢弃。"""
    if dimension == "valuation":
        if text.startswith(("相对有效同行", "相对行业PE溢价")):
            return ["target_pe_premium"]
        if text.startswith("估值数据"):
            return ["pe_ttm", "pb"]
        if text.startswith("PS"):
            return ["ps_ttm"]
        if any(word in text for word in ("历史", "分位区间", "长窗", "日历")):
            return ["pe_percentile", "pb_percentile"]
        if "PS" in text:
            return ["ps_ttm"]
        if "总股本" in text:
            return ["total_shares"]
        if "市值" in text or "CNY" in text or "汇率" in text:
            return ["market_cap"]
        return ["pe_ttm", "pb", "ps_ttm"]
    groups = [
        (("普通权益",), ["common_equity"]),
        (("ROE",), ["roe"]),
        (("毛利率",), ["gross_margin"]),
        (("净利率", "净利润÷"), ["net_margin"]),
        (("利润率",), ["gross_margin", "net_margin"]),
        (("现金流", "资本开支"), ["operating_cash_flow", "capital_expenditure", "free_cash_flow",
                                   "capital_expenditure_latest", "free_cash_flow_latest"]),
        (("同比", "同期"), ["revenue_growth_yoy", "profit_growth_yoy", "deducted_profit_growth_yoy", "cash_flow_growth_yoy"]),
        (("量比",), ["volume_ratio"]),
        (("股息率",), ["dividend_yield"]),
        (("SEC",), ["reported_revenue", "reported_revenue_ttm"]),
        (("单季", "营业收入"), ["revenue", "net_profit"]),
    ]
    for words, fields in groups:
        if any(word in text for word in words):
            return fields
    return ["total_assets", "total_equity", "revenue"]


def _linked_notes(dimension: str, shown: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    """唯一的表注出口：先绑定表内字段，再统一编号、合并和去重。"""
    values = {re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+$", "", key): value for key, value in shown.items()}
    groups: dict[tuple[str, ...], list[str]] = {}
    for note in dict.fromkeys(notes):
        text = str(note).strip()
        if not text:
            continue
        # 长期数值及其来源/区间是一条事实，放入表内，不能删除或拆散。
        match = re.match(r"^((?:PE|PB) 近[三五]年历史)[:：](.*)", text)
        if dimension == "valuation" and match:
            values[match[1]] = match[2].strip()
            continue
        if dimension == "valuation":
            shares = re.search(r"总股本[：:]\s*([\d,.]+)\s*股?", text)
            if shares:
                values["总股本"] = shares[1].rstrip(".") + " 股"
                evidence = text.split("；", 1)
                if len(evidence) == 1:
                    continue
                text = "总股本来源：" + evidence[1]
            if text.startswith("源证据（未折算）：总市值"):
                evidence = text.split("；", 1)
                if len(evidence) == 1:
                    continue
                text = "总市值来源：" + evidence[1]
        if text.startswith(("展示 CNY", "行情价格及区间高低价：展示 CNY", "当前报价：展示 CNY",
                            "总市值：展示 CNY", "同行市值：展示 CNY")):
            text = _currency_note(text)
        text = re.sub(r"^[⁰¹²³⁴⁵⁶⁷⁸⁹]+\s*", "", text)
        fields = _note_fields(text, dimension)
        labels = tuple(metric_display_name(key) for key in fields if metric_display_name(key) in values)
        if not labels:
            if not values:
                values["数据口径"] = "见表注"
            labels = (next(iter(values)),)
        body = re.sub(r"^[⁰¹²³⁴⁵⁶⁷⁸⁹]+\s*", "", text)
        if body not in groups.setdefault(labels, []):
            groups[labels].append(body)
    return number_table_notes(values, groups)


def annotate_table(lines: list[str]) -> list[str]:
    """多列表也遵守同一约束：说明合并到有表头引用的短注。"""
    table = [line for line in lines if line.startswith("|")]
    notes = list(dict.fromkeys(line.strip().lstrip("- ") for line in lines if line.strip() and not line.startswith("|")))
    if not notes:
        return lines
    if not table:
        table = ["| 项目 | 状态 |", "| --- | --- |", "| 可比数据 | 暂无可比较指标 |"]
    body = "；".join(dict.fromkeys(re.sub(r"^[⁰¹²³⁴⁵⁶⁷⁸⁹]+\s*", "", note).rstrip("。；") for note in notes))
    header = table[0].split("|")[1].strip()
    view = number_table_notes({header: ""}, {(header,): [body]})
    table[0] = table[0].replace(header, next(iter(view["metrics"])), 1)
    return [*table, "", *view["notes"]]


def index_valuation_table(display: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    """指数已有说明列，去掉重复期间/口径，剩余限制绑定估值表头。"""
    result = dict(display)
    rows = [dict(row) for row in display.get("rows", [])]
    row_evidence = "；".join(str(row.get("note", "")) for row in rows)
    kept = []
    sources = []
    for item in [*display.get("details", []), *notes]:
        text = str(item).strip()
        label, separator, value = text.partition("：")
        if separator and value and value in row_evidence and ("日期" in label or "口径" in label):
            continue
        if "官方来源" in label and value.startswith("https://"):
            sources.append(value)
        elif text and text not in kept:
            kept.append(text)
    if sources:
        kept.append("官方来源：" + "、".join(dict.fromkeys(sources)))
    if kept:
        view = number_table_notes({"指标": ""}, {("指标",): kept})
        result["table_note_marker"] = next(iter(view["metrics"]))[len("指标"):]
        result["table_notes"] = view["notes"]
        if not rows:
            rows = [{"label": "估值说明", "value": "见表注", "note": "当前无展示指标"}]
    else:
        result["table_note_marker"] = ""
        result["table_notes"] = []
    result["rows"] = rows
    return result


def index_method_table(section: dict[str, Any]) -> dict[str, Any]:
    """指数技术/策略说明也绑定表头，避免各输出面再次独立追加段落。"""
    display = dict(section.get("display") or {})
    metrics = section.get("metrics") or {}
    notes = list(dict.fromkeys(str(note).strip() for note in [metrics.get("methodology"), *metrics.get("errors", [])] if note))
    view = number_table_notes({"指标": ""}, {("指标",): notes})
    display["table_notes"] = view["notes"]
    display["table_note_marker"] = next(iter(view["metrics"]))[len("指标"):]
    if notes and not display.get("rows"):
        display["rows"] = [{"label": "数据口径", "value": "见表注", "note": "当前无展示指标"}]
    return display


def valuation_window_notes(cutoff: str) -> list[str]:
    """窗口与计算性质迁入短注，不因截短标题丢失。"""
    notes = []
    for labels, start, end, count in re.findall(
            r"([A-Z、]+)分位区间：([0-9-]+) 至 ([0-9-]+)，(\d+)个有效样本", cutoff):
        notes.append(f"{labels}分位区间：{start}—{end}，{count} 个有效样本。")
    if "派生" in cutoff:
        notes.append("历史估值由本次财务分母派生，仅参考。")
    elif "百度" in cutoff:
        notes.append("历史估值来自百度实测曲线。")
    elif "东财" in cutoff:
        notes.append("历史估值来自东财日频估值历史。")
    if "不保证覆盖上市以来完整历史" in cutoff:
        notes.append("历史区间有限，不代表上市以来完整历史。")
    return notes


def compact_sentiment(metrics: dict[str, Any]) -> str:
    """保留消息、来源和风险语义，压缩重复解释。"""
    from report.formatter import (
        STOCK_NEWS_DISPLAY_LIMIT,
        _news_url,
        sentiment_display_items,
    )
    from report.peer_comparison import _table, _text
    from utils.numbers import is_finite_number

    risk, score = metrics.get("risk_score"), metrics.get("participation_score")
    view = number_table_notes({
        "舆情风险分": f"{risk:.1f}/10" if is_finite_number(risk) else "无法评估",
        "综合参与分": f"{score:.1f}/10" if is_finite_number(score) else "暂无，暂不参与综合评分",
        "配置权重": "5%",
        "评估窗口": f"{metrics.get('window_start') or '未记录'}—{metrics.get('window_end') or '未记录'}",
        "有效评估消息": f"{metrics.get('evaluated_count', 0)} 条",
    }, {("舆情风险分",): ["越高风险越大；有限样本不代表完整舆情。"]})
    lines = [_table(["项目", "结果"], [[label, value] for label, value in view["metrics"].items()]), "", *view["notes"]]
    reason = str(metrics.get("assessment_reason") or "")
    if reason:
        lines += ["", "相关消息信息不足，本期不计分。" if "内容不足以评估" in reason else _text(reason)]
    if risk == 0:
        lines += ["", "有效样本未识别到负面，不代表全市场无风险。"]
    rows = []
    for item in sentiment_display_items(metrics)[:STOCK_NEWS_DISPLAY_LIMIT]:
        state = ("与目标无直接关联" if item.get("related") is False else "不可评估" if item.get("assessable") is False
                 else "有效相关样本" if item.get("related") is True and item.get("assessable") is True else "未评估")
        sources = []
        for link in item.get("links") or [{"url": item.get("url"), "media": item.get("media") or item.get("source")}]:
            label = _text(link.get("media") or link.get("source") or "来源未记录")
            url = _news_url(link.get("url"))
            sources.append(f"[{label}]({url})" if url else label)
        rows.append([str(item.get("publish_date") or item.get("published_at") or "未记录")[:10],
                     item.get("title", ""), "、".join(sources), state])
    if rows:
        lines += ["", "#### 采集样本与来源", "", _table(["日期", "标题／事件", "来源", "评估状态"], rows)]
    return "\n".join(lines)
