"""确定性分析共用的数值有效性规则，不负责展示或变更原值。"""
import math

# 原有中文单位解析保持兼容。

# 注意：'万亿' 必须排在 '亿' 之前，否则 '1.2万亿' 会误配 '亿'
_UNITS = [("万亿", 1e12), ("亿", 1e8), ("万", 1e4)]
_NULL_TOKENS = {"", "-", "--", "None", "none", "nan", "NaN"}


def parse_cn_number(value) -> float | None:
    """解析带中文单位的数值。无法解析或表示缺失时返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip().replace(",", "")
    if s in _NULL_TOKENS:
        return None

    for unit, mult in _UNITS:
        if s.endswith(unit):
            num_part = s[: -len(unit)]
            try:
                return float(num_part) * mult
            except ValueError:
                return None

    # 处理百分比 "12.5%" → 12.5
    if s.endswith("%"):
        try:
            return float(s[:-1])
        except ValueError:
            return None

    try:
        return float(s)
    except ValueError:
        return None


def is_finite_number(value: object) -> bool:
    """布尔值和数字字符串不是已验证的数值。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_positive_finite(value: object) -> bool:
    """估值倍数必须有限且为正，才能用于高低估比较。"""
    return is_finite_number(value) and isinstance(value, (int, float)) and value > 0


def is_valid_percentile(value: object) -> bool:
    """分位是百分数单位，0 和 100 均为有效边界。"""
    return is_finite_number(value) and isinstance(value, (int, float)) and 0 <= value <= 100
