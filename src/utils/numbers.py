"""中文单位数字解析 -- 将 '3.54亿'、'7217.13万' 等转为 float"""

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

    try:
        return float(s)
    except ValueError:
        return None
