"""保存订阅时批量解析标的类型和显示名称。"""
import re

from data.index_mapping import IndexMapping
from push.models import Subscription, SubscriptionSymbol
from push.symbol_search import cached_stock_names
from utils.config import Config
from utils.symbols import (
    normalize_index_symbol,
    normalize_symbol,
    validate_index_symbol,
    validate_symbol,
)

_OVERSEAS = re.compile(r"^[A-Z]{2,10}$")


def resolve_subscription(sub: Subscription) -> Subscription:
    """在写库之前固定显示类型与名称；股票名称复用搜索目录。"""
    mapping = IndexMapping()
    resolved: list[SubscriptionSymbol] = []
    stock_codes: set[str] = set()
    for item in sub.symbols:
        raw = item.symbol.strip()
        index_code = normalize_index_symbol(raw)
        entry = mapping.lookup(index_code)
        is_index = item.kind == "index" or (item.kind == "auto" and (entry is not None or bool(_OVERSEAS.fullmatch(raw.upper()))))
        if is_index:
            if not validate_index_symbol(raw):
                raise ValueError(f"无效的指数代码: {raw}")
            resolved.append(SubscriptionSymbol(symbol=index_code, kind="index",
                                               index_style=item.index_style or (entry.index_style if entry else None),
                                               display_name=entry.name if entry else item.display_name))
        else:
            if not validate_symbol(raw):
                raise ValueError(f"无效的股票代码: {raw}")
            code = normalize_symbol(raw)
            if not item.display_name.strip():
                stock_codes.add(code)
            resolved.append(SubscriptionSymbol(symbol=code, kind="stock",
                                               display_name=item.display_name))
    names = (cached_stock_names(Config().config_dir) or {}) if stock_codes else {}
    sub.symbols = [
        item.model_copy(update={"display_name": names.get(item.symbol, item.display_name)})
        if item.kind == "stock" else item for item in resolved
    ]
    return sub
