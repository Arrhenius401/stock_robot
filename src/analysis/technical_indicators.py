"""跨市场共享的确定性技术事实，不含评分和交易规则。"""

from dataclasses import dataclass
from statistics import mean


def _ema_line(data: list[float], period: int) -> list[float | None]:
    if not data:
        return []
    if len(data) < period:
        return [None] * len(data)
    multiplier = 2 / (period + 1)
    line: list[float | None] = [None] * (period - 1)
    ema = sum(data[:period]) / period
    line.append(ema)
    for value in data[period:]:
        ema = (value - ema) * multiplier + ema
        line.append(ema)
    return line


@dataclass(frozen=True, slots=True)
class MacdValues:
    """MACD 结果值对象。"""

    dif: float
    dea: float
    bar: float


def calculate_macd(closes: list[float]) -> MacdValues:
    """保留原评分算法，短历史的缺省值仅供旧评分规则兼容。"""
    if not closes:
        return MacdValues(dif=0.0, dea=0.0, bar=0.0)

    ema12_line = _ema_line(closes, 12)
    ema26_line = _ema_line(closes, 26)
    dif_line = [
        ema12 - ema26
        for ema12, ema26 in zip(ema12_line, ema26_line)
        if ema12 is not None and ema26 is not None
    ]
    if not dif_line:
        return MacdValues(dif=0.0, dea=0.0, bar=0.0)
    dif = dif_line[-1]
    if len(dif_line) < 9:
        dea = dif
    else:
        dea_line = _ema_line(dif_line, 9)
        dea_value = dea_line[-1] if dea_line else None
        dea = dea_value if dea_value is not None else dif
    return MacdValues(dif=dif, dea=dea, bar=2 * (dif - dea))


def moving_average(closes: list[float], window: int) -> float | None:
    """完整窗口的收盘价均值，样本不足时不使用短窗代替。"""
    return mean(closes[-window:]) if len(closes) >= window else None


def volume_ratio(volumes: list[int], *, require_complete_window: bool = True) -> float | None:
    """最近五日均量 / 此前二十日均量；旧评分可显式允许短窗。"""
    if not volumes or (require_complete_window and len(volumes) < 25):
        return None
    previous = volumes[-25:-5]
    if not previous:
        return None
    denominator = mean(previous)
    return mean(volumes[-5:]) / denominator if denominator > 0 else None


def calculate_technical_indicators(closes: list[float], volumes: list[int]) -> dict[str, float | None]:
    """消费按日期升序的有效行情，输出可披露的完整窗口指标。"""
    if not closes:
        return {}
    macd = calculate_macd(closes) if len(closes) >= 34 else None
    return {
        "latest_close": closes[-1],
        "ma_5": moving_average(closes, 5),
        "ma_20": moving_average(closes, 20),
        "ma_60": moving_average(closes, 60),
        "volume_ratio": volume_ratio(volumes),
        "macd_dif": macd.dif if macd is not None else None,
        "macd_dea": macd.dea if macd is not None else None,
        "macd_bar": macd.bar if macd is not None else None,
    }
