"""中文公司名的标题匹配别名：A 股/港股新闻按标题公司简称过滤时共用。"""
_PREFIXES = (
    "中国", "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林",
    "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北",
    "湖南", "广东", "广西", "海南", "四川", "贵州", "云南", "陕西", "甘肃",
    "青海", "宁夏", "新疆", "内蒙古", "香港", "深圳",
)
_SUFFIXES = ("控股", "集团", "有限公司", "股份", "公司", "国际", "科技", "实业", "企业")
_GENERIC = frozenset({
    "中国", "时代", "银行", "科技", "集团", "股份", "公司", "国际", "控股",
    "实业", "企业", "证券", "基金", "能源", "电力", "汽车", "医药", "地产",
    "投资", "发展", "建设", "交通",
})


def name_aliases(name: str, symbol: str = "") -> tuple[str, ...]:
    """返回可用于标题匹配的简称；代码、空名或纯数字名称返回空，表示不过滤。"""
    value = name.strip()
    if not value or value.upper().endswith(".HK") or value.isdigit():
        return ()
    aliases = {value}
    stripped = value
    for prefix in _PREFIXES:
        if stripped.startswith(prefix) and len(stripped) > len(prefix) + 1:
            stripped = stripped[len(prefix):]
            aliases.add(stripped)
            break
    for suffix in _SUFFIXES:
        if stripped.endswith(suffix) and len(stripped) > len(suffix) + 1:
            stripped = stripped[: -len(suffix)]
            aliases.add(stripped)
            break
    if len(value) >= 4:
        for alias in (value[:2], value[-2:]):
            if alias not in _GENERIC:
                aliases.add(alias)
    return tuple(sorted(aliases, key=len, reverse=True))
