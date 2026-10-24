"""报告格式化器 — 文件保存和终端渲染"""
import hashlib
import html
import json
import logging
import math
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from rich.console import Group
from rich.markdown import Markdown
from rich.table import Table

from utils.numbers import is_finite_number

logger = logging.getLogger(__name__)


def format_hk_news_markdown(metrics: dict[str, Any]) -> str:
    """沿用普通新闻项目列表，只展示标题、时间和媒体，不输出链接。"""
    def text(value: Any) -> str:
        clean = html.escape(str(value), quote=False).replace("\n", " ").replace("\r", " ")
        return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", clean)

    def stamp(value: Any) -> str:
        try:
            return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return str(value)

    lines = [f"> {text(metrics.get('news_disclaimer', ''))}"]
    for item in metrics.get("news_items", [])[:20]:
        media = "、".join(dict.fromkeys(str(link.get("media") or "来源未标注") for link in item.get("links", []))) or "来源未标注"
        lines.append(f"\n- {text(item.get('title', ''))}（{text(stamp(item.get('published_at', '')))} · {text(media)}）")
    return "\n".join(lines)

_STOCK_SYMBOL_PATTERN = r"(?:\d{6}|\d{5}\.HK)"

_SEP_RE = re.compile(r'^\|[-\s:|]+\|$')

METRIC_DISPLAY_NAMES = {
    "operating_trends": "经营趋势",
    "trend_note": "趋势分析限制",
    "cash_flow_source": "经营现金流来源",
    "deducted_net_profit": "扣非净利润",
    "amount_basis": "财务金额口径",
    "revenue_basis": "营收科目口径",
    "gross_margin_basis": "毛利率计算口径",
    "valuation_applicability": "估值适用性",
    "historical_valuation_applicability": "独立历史分位适用性",
    "applicable": "可用于高低估判断",
    "reason": "原因",
    "pe_snapshot_basis": "单张PE计算口径",
    "pe_snapshot_source_url": "单张PE官方来源",
    "pe_snapshot_as_of": "单张PE数据日期",
    "pe_snapshot": "官方单张市盈率",
    "pe_as_of": "PE数据日期",
    "pb_as_of": "PB数据日期",
    "pe_basis": "PE计算口径",
    "pb_basis": "PB计算口径",
    "pe_source_url": "PE官方来源",
    "pb_source_url": "PB官方来源",
    "pe_sample_count": "PE有效交易日样本数",
    "pb_sample_count": "PB有效交易日样本数",
    "valuation_notes": "估值数据覆盖说明",

    "latest_quarter": "最新财报季度",
    "report_period": "最新报告期",
    "revenue": "营业收入",
    "net_profit": "净利润",
    "total_assets": "总资产",
    "total_equity": "股东权益",
    "operating_cash_flow": "经营活动现金流",
    "operating_cash_flow_per_share": "每股经营现金流",
    "revenue_growth_yoy": "营收同比增长",
    "profit_growth_yoy": "净利润同比增长",
    "roe": "ROE",
    "gross_margin": "毛利率",
    "latest_close": "最新收盘价",
    "ma_5": "5 日均线",
    "ma_20": "20 日均线",
    "ma_60": "60 日均线",
    "volume_ratio": "近5日与此前20日均量比",
    "macd_dif": "MACD DIF",
    "macd_dea": "MACD DEA",
    "macd_bar": "MACD 柱值",
    "year_high": "近一年最高价",
    "year_low": "近一年最低价",
    "pe_ttm": "PE(TTM)",
    "pb": "PB",
    "ps_ttm": "PS(TTM)",
    "pe_percentile": "PE 分位",
    "pb_percentile": "PB 分位",
    "dividend_yield": "股息率",
    "valuation_valid": "估值样本有效",
    "percentile_lookback_years": "分位回看年限",
    "sample_start": "样本起始日期",
    "sample_end": "样本结束日期",
    "industry": "所属行业",
    "sector": "所属板块",
    "peer_scope": "同业口径",
    "peer_industry": "可比行业",
    "peers": "同行业公司",
    "top_peers": "头部同行公司",
    "roe_trend": "净资产收益率走势",
    "quarter": "报告期",
    "headlines": "近期新闻",
    "top_headlines": "近期要闻",
    "peer_count": "有效同行数（不含本公司）",
    "industry_median_pe": "行业 PE(TTM) 中位数",
    "industry_median_pb": "行业 PB 中位数",
    "target_pe_premium": "相对行业 PE 溢价",
    "target_market_cap_rank": "行业市值排名",
    "classification_scheme": "分类体系",
    "industry_source": "行业数据来源",
    "industry_data_date": "行业数据日期",
    "industry_scheme_note": "分类口径说明",
    "target_pe": "目标 PE（行业池口径）",
    "target_pb": "目标 PB（行业池口径）",
    "target_pe_percentile": "相对行业 PE 分位",
    "target_pb_percentile": "相对行业 PB 分位",
    "peer_pe_sample": "有效 PE 样本数",
    "peer_pb_sample": "有效 PB 样本数",
    "headline_count": "新闻数量",
    "date": "数据日期",
    "north_bound": "北向资金净流入",
    "main_net_inflow": "主力资金净流入",
    "margin_balance": "融资余额",
    "pmi": "PMI",
    "shibor_3m": "3 月期 Shibor",
    "cpi_yoy": "CPI 同比",
    "usd_cny": "美元兑人民币",
    "shibor_percentile": "Shibor 分位",
    "pmi_percentile": "PMI 分位",
    "tag": "信号标签",
}

RISK_DISPLAY_NAMES = {
    "roe_low": "净资产收益率偏低",
    "high_debt": "资产负债率偏高",
    "cash_flow_mismatch": "经营现金流与净利润不匹配",
    "high_pe_premium": "市盈率分位偏高",
    "industry_weak_margin": "毛利率弱于行业",
    "bearish_ma": "均线呈空头排列",
    "volume_bearish": "放量下跌风险",
    "major_negative_news": "存在重大负面舆情",
}


def metric_display_name(key: object) -> str:
    """返回指标的中文显示名，未知指标保留原键名。"""
    text = str(key)
    return METRIC_DISPLAY_NAMES.get(text, text)


_PRICE_KEYS = {"latest_close", "latest_price", "year_high", "year_low", "ma_5", "ma_20", "ma_60"}
_MULTIPLE_KEYS = {"pe_ttm", "pe_snapshot", "pb", "ps_ttm", "industry_median_pe", "industry_median_pb",
                  "target_pe", "target_pb"}
_RATIO_KEYS = {"roe", "gross_margin", "revenue_growth_yoy", "profit_growth_yoy", "industry_median_gross_margin"}
_PERCENT_KEYS = {"change_pct", "pe_percentile", "pb_percentile", "target_pe_premium", "dividend_yield",
                 "shibor_3m", "cpi_yoy", "shibor_percentile", "pmi_percentile",
                 "target_pe_percentile", "target_pb_percentile"}
# 这些字段在适配层按基本货币单位记录；币种由各报告上下文披露，不由上市地推断。
_AMOUNT_KEYS = {"revenue", "net_profit", "deducted_net_profit", "total_assets", "total_equity", "common_equity",
                "operating_cash_flow", "market_cap"}
_ONE_DIGIT_KEYS = {"score", "base_score", "final_score", "risk_deduction"}
_THREE_DIGIT_KEYS = {"macd_dif", "macd_dea", "macd_bar"}
_TWO_DIGIT_KEYS = {"volume_ratio", "operating_cash_flow_per_share", "basic_eps", "pmi", "usd_cny"}


def _fixed_number(value: float, digits: int) -> str:
    """避免二进制尾数和负零；极小非零价格由调用者另行保留。"""
    text = f"{value:,.{digits}f}"
    return text.removeprefix("-") if round(value, digits) == 0 else text


def format_metric_value(key: object, value: Any) -> Any:
    """只按已知字段口径格式化显示，不改动计算或持久化数值。"""
    key = str(key)
    if value is None or (isinstance(value, str) and value == "N/A"):
        return "暂无数据"
    if isinstance(value, float) and not math.isfinite(value):
        return "数据异常"
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not is_finite_number(value):
        return value
    if key == "north_bound":
        return (f"{value:.8g}" if value != 0 and round(value, 2) == 0 else _fixed_number(value, 2)) + " 亿"
    if key in {"main_net_inflow", "margin_balance"}:
        return (f"{value:.8g}" if value != 0 and round(value, 2) == 0 else _fixed_number(value, 2)) + "（原始单位未记录）"
    if key in _AMOUNT_KEYS:
        unit, divisor = (" 亿", 1e8) if abs(value) >= 1e8 else (" 万", 1e4) if abs(value) >= 1e4 else ("", 1)
        return _fixed_number(value / divisor, 2) + unit
    if key in _RATIO_KEYS:
        return _fixed_number(value * 100, 2) + "%"
    if key in _PERCENT_KEYS or key.endswith("_pct"):
        return _fixed_number(value, 2) + "%"
    if key in _MULTIPLE_KEYS:
        text = _fixed_number(value, 2)
        return text + "（不适用于高低估判断）" if value <= 0 else text
    if key in _PRICE_KEYS:
        return f"{value:.8g}" if value != 0 and round(value, 2) == 0 else _fixed_number(value, 2)
    if key in _THREE_DIGIT_KEYS:
        return _fixed_number(value, 3)
    if key in _ONE_DIGIT_KEYS:
        return _fixed_number(value, 1)
    if key in _TWO_DIGIT_KEYS:
        return _fixed_number(value, 2)
    return value


def format_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """模型事实及嵌套表格使用字段语义，保留代码、日期与未知字段。"""
    def display(key: str, value: Any) -> Any:
        if isinstance(value, dict):
            return format_metrics(value)
        if isinstance(value, list):
            return [display(key, item) for item in value]
        return format_metric_value(key, value)

    formatted = {key: display(key, value) for key, value in metrics.items()}
    applicability = metrics.get("valuation_applicability")
    if isinstance(applicability, dict):
        for snapshot, derived in (("pe_ttm", "pe_percentile"), ("pb", "pb_percentile")):
            historical = metrics.get("historical_valuation_applicability", {})
            historical_rule = historical.get(derived, {}) if isinstance(historical, dict) else {}
            # 港股同源历史已独立校验；快照缺项不能覆盖其有效分位，旧 A 股路径不变。
            if (isinstance(historical_rule, dict) and historical_rule.get("applicable") is True
                    and metrics.get(derived) is not None):
                continue
            rule = applicability.get(snapshot, {})
            if isinstance(rule, dict) and not rule.get("applicable"):
                formatted[derived] = "不适用" if metrics.get(snapshot) is not None else rule.get("reason", "暂无数据")
            elif metrics.get(derived) is None:
                formatted[derived] = "历史样本不足"
        if metrics.get("target_pe_premium") is None:
            formatted["target_pe_premium"] = "不适用" if not applicability.get("pe_ttm", {}).get("applicable") else "暂无可比数据"
    return formatted


def risk_display_name(key: object) -> str:
    """返回风险标签的中文显示名，未知标签保留原值。"""
    text = str(key)
    return RISK_DISPLAY_NAMES.get(text, text)


def _is_separator(line: str) -> bool:
    """检测 Markdown 表格分隔行"""
    return bool(_SEP_RE.match(line.strip()))


def _parse_table(lines: list[str]) -> Table:
    """将 Markdown 表格行转为 Rich Table"""
    headers = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    sep_cells = [c.strip() for c in lines[1].strip().strip("|").split("|")]

    table = Table(show_header=True, header_style="bold")
    for header, sep in zip(headers, sep_cells):
        if sep.startswith(":") and sep.endswith(":"):
            justify = "center"
        elif sep.endswith(":"):
            justify = "right"
        else:
            justify = "left"
        table.add_column(header, justify=justify)

    for line in lines[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        table.add_row(*[c if c else "" for c in cells])

    return table


def _archive_value(value: Any) -> Any:
    """复制 JSON 事实，清除临时重试标识及非有限数值。"""
    if isinstance(value, dict):
        return {str(key): _archive_value(item) for key, item in value.items()
                if key not in {"commentary_retry_id", "report_token"}}
    if isinstance(value, (list, tuple)):
        return [_archive_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def validate_stock_payload(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    """标的和渲染所需容器必须真实匹配，不能拼接其他股票的存档。"""
    payload = _archive_value(payload)
    actual = payload.get("symbol") or payload.get("code")
    if actual != symbol:
        raise ValueError("快照标的与报告不匹配")
    if any(payload.get(key) not in (None, symbol) for key in ("symbol", "code")):
        raise ValueError("快照标的字段冲突")
    for key in ("overview", "score", "dimensions"):
        if not isinstance(payload.get(key), dict):
            raise TypeError(f"快照 {key} 结构无效")
    for key in ("base", "final", "risk_deduction"):
        value = payload["score"].get(key)
        if value is not None and type(value) not in (int, float):
            raise TypeError("快照评分结构无效")
    for value in payload["dimensions"].values():
        if not isinstance(value, dict) or not isinstance(value.get("metrics", {}), dict):
            raise TypeError("快照维度结构无效")
    for key in ("name", "commentary", "commentary_status", "generated_at"):
        if key in payload and not isinstance(payload[key], str):
            raise TypeError(f"快照 {key} 结构无效")
    for key in ("score_rows", "pending_verifications", "comments"):
        if key in payload and not isinstance(payload[key], list):
            raise TypeError(f"快照 {key} 结构无效")
    quality = payload.get("data_quality")
    if quality is not None:
        if not isinstance(quality, dict) or not isinstance(quality.get("dimensions", []), list):
            raise TypeError("快照数据覆盖结构无效")
        for dimension in quality.get("dimensions", []):
            if not isinstance(dimension, dict) or not isinstance(dimension.get("fields", []), list):
                raise TypeError("快照数据依据结构无效")
            if any(not isinstance(item, dict) for item in dimension.get("fields", [])):
                raise TypeError("快照数据依据字段无效")
    if payload.get("signal") is not None and not isinstance(payload["signal"], dict):
        raise TypeError("快照信号结构无效")
    return _archive_value(payload)


def _atomic_write(path: Path, content: bytes) -> None:
    """同目录临时文件替换；两种产物通过摘要验证批次而非混合读取。"""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError as exc:
                logger.debug("快照临时文件未清理：%s（%s）", temporary.name, exc)


def update_stock_snapshot(markdown: str, path: Path, payload: dict[str, Any]) -> None:
    """更新原报告及配套事实，不允许越过个股报告目录。"""
    path = Path(path)
    if (path.suffix.lower() != ".md" or len(path.parents) < 4
            or path.parents[2].name != "stock"
            or not re.fullmatch(_STOCK_SYMBOL_PATTERN, path.parent.parent.name)):
        raise ValueError("个股报告路径无效")
    root = path.parents[3].resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError("个股报告路径超出报告目录")
    symbol = path.parent.parent.name
    data = validate_stock_payload(payload, symbol)
    content = markdown.encode("utf-8")
    wrapper = {"schema_version": 1, "markdown_sha256": hashlib.sha256(content).hexdigest(),
               "payload": data}
    snapshot = json.dumps(wrapper, ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")
    _atomic_write(path, content)
    snapshot_path = path.with_suffix(".report.json")
    try:
        _atomic_write(snapshot_path, snapshot)
    except OSError as exc:
        # 原文更新成功后不回滚；快照只是只读展示的可选增强。
        logger.warning("原文已更新，报告快照保存失败：%s（%s）", path.name, exc)
        try:
            snapshot_path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            logger.warning("旧报告快照未清理：%s（%s）", snapshot_path.name, cleanup_error)


class ReportFormatter:
    @staticmethod
    def save(report: str, symbol: str, output_dir: Path | None = None,
             category: Literal["stock", "index"] = "stock",
             stock_payload: dict[str, Any] | None = None) -> Path:
        if output_dir is None:
            output_dir = Path.cwd() / "reports"
        output_dir = Path(output_dir)
        if (category not in ("stock", "index")
                or not re.fullmatch(
                    _STOCK_SYMBOL_PATTERN if category == "stock"
                    else r"[A-Za-z0-9][A-Za-z0-9._-]*", symbol)):
            raise ValueError("报告分类或标的无效")
        generated_at = datetime.now().astimezone()
        report_dir = output_dir / category / symbol / generated_at.strftime("%Y-%m")
        if not report_dir.resolve().is_relative_to(output_dir.resolve()):
            raise ValueError("报告路径超出保存目录")
        report_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{symbol}_{generated_at.strftime('%Y%m%d_%H%M%S')}.md"
        filepath = report_dir / filename
        if category == "stock" and stock_payload is not None:
            try:
                update_stock_snapshot(report, filepath, stock_payload)
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("报告快照保存失败，保留 Markdown：%s", exc)
                _atomic_write(filepath, report.encode("utf-8"))
                snapshot_path = filepath.with_suffix(".report.json")
                if snapshot_path.exists():
                    snapshot_path.unlink()
        else:
            _atomic_write(filepath, report.encode("utf-8"))
            if category == "stock":
                snapshot_path = filepath.with_suffix(".report.json")
                if snapshot_path.exists():
                    snapshot_path.unlink()
        return filepath

    update_stock_snapshot = staticmethod(update_stock_snapshot)

    @staticmethod
    def to_rich_markdown(report: str) -> Markdown | Group:
        """将报告转为 Rich 可渲染对象，表格块用 Table 渲染"""
        lines = report.split("\n")
        renderables = []
        buf: list[str] = []
        i = 0

        while i < len(lines):
            line = lines[i]
            # 检测表格：当前行以 | 开头且下一行是分隔行
            stripped = line.strip()
            if (stripped.startswith("|") and stripped.endswith("|")
                    and i + 1 < len(lines)
                    and _is_separator(lines[i + 1])):
                if buf:
                    renderables.append(Markdown("\n".join(buf)))
                    buf = []

                # 收集表格行
                tbl_lines = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    tbl_lines.append(lines[i])
                    i += 1

                try:
                    renderables.append(_parse_table(tbl_lines))
                except Exception:  # noqa: BLE001 — 表格解析失败回退原始 Markdown
                    renderables.append(Markdown("\n".join(tbl_lines)))
            else:
                buf.append(line)
                i += 1

        if buf:
            renderables.append(Markdown("\n".join(buf)))

        if not renderables:
            return Markdown("")
        if len(renderables) == 1:
            return renderables[0]
        return Group(*renderables)
