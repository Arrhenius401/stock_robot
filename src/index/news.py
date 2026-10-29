"""指数新闻元数据清洗：缺失值不转文本，不以采集日补发布时间。"""
import re
from datetime import date, datetime
from typing import Any

import pandas as pd
from pandas.api.types import is_scalar


def news_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if not is_scalar(value) or bool(pd.isna(value)):
            continue
        text = str(value).strip()
        if text and text.casefold() not in {'nan', 'nat', '<na>', 'none', 'null'}:
            return text
    return ''


def publication_time(row: dict[str, Any]) -> str | None:
    for key in ('published_at', 'pub_time', 'publish_time', '时间'):
        value = row.get(key)
        if not is_scalar(value) or bool(pd.isna(value)):
            continue
        text = news_text({key: value}, key)
        # 没有明确年份的时间、纯数字时间戳不猜测单位或补当前日期。
        if not isinstance(value, (date, datetime)) and not re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s|T|$)', text):
            continue
        try:
            parsed = pd.to_datetime(text, errors='coerce')
        except (ValueError, TypeError, OverflowError):
            continue
        if not bool(pd.isna(parsed)):
            return text
    return None


def normalize_news_item(row: dict[str, Any]) -> dict[str, Any]:
    return {'title': news_text(row, 'title', 'summary'),
            'published_at': publication_time(row),
            'source': news_text(row, 'source', 'media') or '未记录',
            'url': news_text(row, 'url', '链接')}
