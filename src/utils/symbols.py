"""股票代码工具 — 标准化、校验、名称解析"""
import logging
import re
from datetime import datetime
from typing import Any

from data.collection_state import ValueResult, make_record
from utils.retry import retry_on_network_error

logger = logging.getLogger(__name__)


@retry_on_network_error()
def _ak_code_name():
    import akshare as ak
    return ak.stock_info_a_code_name()


def _extract_prefix_and_digits(raw: str) -> tuple[str, str]:
    """从原始输入中提取市场前缀和数字代码"""
    m = re.match(r"^(sh|sz|SH|SZ)?(\d+)$", raw.strip())
    if m is None:
        return "", ""
    return (m.group(1) or "").lower(), m.group(2)


def _hk_digits(raw: str) -> str:
    matched = re.fullmatch(r"(?i)(?:hk)?(\d{5})(?:\.hk)?", raw.strip())
    return matched.group(1) if matched else ""


def symbol_market(symbol: str) -> str:
    """返回已校验个股代码的市场标识。"""
    return "hk" if _hk_digits(symbol) else "a-shares"


def _code_to_market(code: str) -> str:
    """根据代码首位判断所属交易所"""
    first = code[0]
    if first in ("6", "8"):
        return "sh"
    elif first in ("0", "3"):
        return "sz"
    return ""


def normalize_symbol(raw: str) -> str:
    """清理前缀并补零到 6 位"""
    hk_code = _hk_digits(raw)
    if hk_code:
        return f"{hk_code}.HK"
    cleaned = re.sub(r"^(sh|sz|SH|SZ)", "", raw.strip())
    return cleaned.zfill(6)


def validate_symbol(symbol: str) -> bool:
    """校验 A 股代码格式，前缀与代码交易所必须一致"""
    if _hk_digits(symbol):
        return True
    prefix, digits = _extract_prefix_and_digits(symbol)
    if len(digits) < 5:
        return False
    code = digits.zfill(6)
    if not re.match(r"^\d{6}$", code):
        return False
    first = code[0]
    if first not in ("0", "3", "6", "8"):
        return False
    if prefix:
        expected = _code_to_market(code)
        if prefix != expected:
            return False
    return True


def resolve_name_result(symbol: str) -> ValueResult[str]:
    """解析名称并保留来源和安全诊断。"""
    source = "东财港股快照" if symbol_market(symbol) == "hk" else "AkShare A股代码名称"
    try:
        if symbol_market(symbol) == "hk":
            from data.hk_eastmoney import stock_snapshot

            snapshot = stock_snapshot(normalize_symbol(symbol).removesuffix(".HK"))
            name = str(snapshot.get("f58") or "").strip()
        else:
            df: Any = _ak_code_name()
            row = df[df["code"] == normalize_symbol(symbol)]
            name = str(row["name"].iloc[0]) if not row.empty else ""
        if name:
            return ValueResult(name, make_record(source, "complete", collected_at=datetime.now().astimezone()))
    except Exception as e:  # noqa: BLE001 — 第三方接口边界，返回安全诊断
        logger.warning("股票名称解析失败 %s: %s", symbol, type(e).__name__)
    return ValueResult(None, make_record(source, "failed", error_kind="fetch_failed",
                                         error_message="股票名称解析失败"))


def resolve_name(symbol: str) -> str:
    """兼容旧调用方，未取得名称时返回空字符串。"""
    return resolve_name_result(symbol).value or ""


def validate_index_symbol(symbol: str) -> bool:
    """校验指数代码格式"""
    import re
    # 支持 sh000001 / sz399001 / 000001 / HSI / SPX 等格式
    cleaned = symbol.strip().upper()
    # 中证策略指数允许 H 加五位数字，避免放宽任意混合代码。
    if re.fullmatch(r"H\d{5}", cleaned):
        return True
    # 海外指数：大写字母组合
    if re.match(r"^[A-Z]{2,10}$", cleaned):
        return True
    # A 股指数：可选前缀 + 数字
    _, digits = _extract_prefix_and_digits(cleaned.lower())
    if not digits or len(digits) < 5:
        return False
    code = digits.zfill(6)
    return bool(re.match(r"^\d{6}$", code))


def normalize_index_symbol(symbol: str) -> str:
    """清理前缀，A 股指数补零到 6 位；海外指数保留大写"""
    import re
    cleaned = symbol.strip().upper()
    # 中证字母数字代码保留大写。
    if re.fullmatch(r"H\d{5}", cleaned):
        return cleaned
    # 海外指数直接返回大写
    if re.match(r"^[A-Z]{2,10}$", cleaned):
        return cleaned
    # A 股指数去前缀、补零
    cleaned_lower = re.sub(r"^(sh|sz|SH|SZ)", "", symbol.strip())
    return cleaned_lower.zfill(6)
