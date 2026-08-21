"""推送内容构建 — 股票/指数的微信摘要与邮箱全文"""
from report.scoring import compute_price_info, compute_score_summary
from report.signal import SIGNAL_LABELS, derive_signal


def build_stock_summary(symbol: str, name: str, results, ctx,
                        signal_cfg) -> str:
    """微信摘要：核心指标 + 操作信号 + 维度评分 + 风险"""
    summary = compute_score_summary(results)
    price = compute_price_info(ctx)
    lines = [f"**{name}**（{symbol}）"]
    if price["latest_price"] is not None:
        pct = price["change_pct"]
        pct_txt = f"（{pct:+.2f}%）" if pct is not None else ""
        lines.append(f"> 最新收盘：{price['latest_price']} {pct_txt}")
    level = derive_signal(summary.final_score, signal_cfg.thresholds)
    action = signal_cfg.actions[level]
    lines.append(f"> 操作信号：**{SIGNAL_LABELS[level]}**｜{action.action}（{action.position}）")
    lines.append(f"> 综合得分：{summary.final_score}/10（风险扣分 {summary.risk_deduction}）")
    for row in summary.score_rows:
        lines.append(f"- {row['label']}：{row['score']}（{row['sufficiency']}）")
    for flag in summary.risk_flags:
        lines.append(f"- ⚠ {flag}")
    return "\n".join(lines)


def build_stock_full(symbol: str, name: str, results, commentary: dict,
                     ctx, signal_cfg) -> str:
    """邮箱全文：复用 build_report 的完整 markdown 报告"""
    from report.scoring import build_report
    return build_report(symbol, name, results, commentary, ctx,
                        signal_cfg=signal_cfg)


_TAG_LABELS = [
    ("tag_technical", "技术"), ("tag_valuation", "估值"),
    ("tag_capital", "资金"), ("tag_macro", "宏观"), ("tag_sentiment", "舆情"),
]


def build_index_summary(report) -> str:
    """指数微信摘要：多空标签 + 综合点评 + 风险"""
    lines = [f"**{report.name}**（{report.code}）", f"> 报告日期：{report.date}"]
    for attr, label in _TAG_LABELS:
        val = getattr(report, attr)
        if val not in ("na", "invalid", None):
            lines.append(f"- {label}：{val}")
    if report.composite_comment:
        lines.append(f"- 综合点评：{report.composite_comment}")
    for risk in report.risk_list:
        lines.append(f"- ⚠ {risk}")
    return "\n".join(lines)


def build_index_full(report) -> str:
    """指数邮箱全文：维度指标 markdown"""
    lines = [f"# {report.name}（{report.code}）", f"报告日期：{report.date}", ""]
    sections = [
        ("技术面", report.section_technical),
        ("估值", report.section_valuation),
        ("资金面", report.section_capital),
        ("宏观", report.section_macro),
        ("舆情", report.section_sentiment),
    ]
    for label, sec in sections:
        if sec is None:
            continue
        lines.append(f"## {label}")
        for k, v in sec.items():
            if isinstance(v, list):
                v = "、".join(str(x) for x in v)
            lines.append(f"- {k}：{v}")
        lines.append("")
    if report.risk_list:
        lines.append("## 风险")
        for risk in report.risk_list:
            lines.append(f"- ⚠ {risk}")
    return "\n".join(lines)
