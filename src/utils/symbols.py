"""股票代码工具 — 标准化、校验、名称解析"""
import re
import logging

logger = logging.getLogger(__name__)


def normalize_symbol(raw: str) -> str:
    """清理前缀并补零到 6 位"""
    cleaned = re.sub(r"^(sh|sz|SH|SZ)", "", raw.strip())
    return cleaned.zfill(6)


def validate_symbol(symbol: str) -> bool:
    """校验 A 股代码格式"""
    # 原始输入必须至少 5 位数字（防止 "123" 被补零为 "000123"）
    raw = symbol.strip().lstrip("sShHzZ")
    if len(raw) < 5:
        return False
    s = normalize_symbol(symbol)
    if not re.match(r"^\d{6}$", s):
        return False
    first = s[0]
    return first in ("0", "3", "6", "8")


def resolve_name(symbol: str) -> str:
    """解析股票代码对应的公司名称"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        row = df[df["code"] == normalize_symbol(symbol)]
        if not row.empty:
            return str(row["name"].iloc[0])
    except Exception as e:
        logger.warning(f"股票名称解析失败 {symbol}: {e}")
    return ""
