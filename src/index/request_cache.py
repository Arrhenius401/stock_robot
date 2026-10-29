"""普通指数分源请求缓存：成功产物保留，失败状态短缓存。"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from data.base import CollectionResult
from data.cache import CacheManager
from data.collection_policies import index_policy_for
from data.collection_state import make_record
from data.schemas import (
    CapitalFlowData,
    DataCollectionRecord,
    IndexPriceData,
    IndexValuationData,
    MacroContext,
    NewsData,
)

_MODELS = {'index_price': IndexPriceData, 'index_valuation': IndexValuationData, 'index_capital_flow': CapitalFlowData, 'index_macro': MacroContext, 'index_sentiment': NewsData}


class IndexRequestCache:
    def __init__(self, path: Path):
        self.cache = CacheManager(path)

    def fetch_cached(self, adapter: Any, symbol: str, kind: str, **kwargs: Any) -> CollectionResult:
        now = datetime.now().astimezone()
        key = f'{symbol}:{kwargs.get("index_style", "")}'
        # 先读新鲜成功或最近失败状态，均不驱逐旧成功产物。
        status = self.cache.get_entry(kind, key, 'status')
        if status is not None and time.time() - status.created_at < status.ttl_seconds:
            raw = json.loads(status.value)
            model = _MODELS[kind]
            record = DataCollectionRecord.model_validate(raw['record']) if raw.get('record') else None
            if record:
                record = record.model_copy(update={'cache_status': 'cache'})
            rows = [model.model_validate(row) for row in raw['rows']]
            if record and record.source in {"", "来源未记录", "未记录"}:
                source = "AkShare宏观PMI／银行间利率接口" if kind == "index_macro" else ""
                if kind == "index_sentiment":
                    from index.news import normalize_news_item
                    items = [normalize_news_item(item) for row in rows for item in getattr(row, "index_items", [])]
                    known = [item["source"] for item in items if item["source"] != "未记录"]
                    hosts = [urlparse(item["url"]).hostname or "" for item in items]
                    source = "；".join(dict.fromkeys(known))
                    if not source and any(host == "caixin.com" or host.endswith(".caixin.com") for host in hosts):
                        source = "财新要闻（缓存原始链接）"
                if source:
                    record = record.model_copy(update={"source": source})
            return CollectionResult(rows, record, raw.get('errors', []))
        result = adapter.fetch_result(symbol, data_type=kind, **kwargs)
        source = getattr(result.rows[0], "_data_source", "") if result.rows else ""
        if source:
            result = CollectionResult(result.rows, make_record(source, "complete", diagnostic=result.record), result.errors)
        policy = index_policy_for(kind)
        state = policy.classify(symbol, result.rows, result.record, None, now) if policy else "failed"
        if not source and result.rows and kind == "index_valuation":
            source = "；".join(dict.fromkeys(getattr(result.rows[0], field, "") for field in ("pe_source_url", "pb_source_url", "pe_snapshot_source_url") if getattr(result.rows[0], field, "")))
        if not source and kind == "index_macro":
            source = "AkShare宏观PMI／银行间利率接口"
        if not source and kind == "index_sentiment":
            source = "；".join(dict.fromkeys(str(item.get("source") or "") for row in result.rows for item in getattr(row, "index_items", []) if item.get("source") not in (None, "", "未记录")))
        result = CollectionResult(result.rows, make_record(source or (result.record.source if result.record else "来源未记录"), state, diagnostic=result.record, collected_at=now), result.errors)
        failed_notes = any("请求失败" in note for row in result.rows for note in getattr(row, "valuation_notes", []))
        successful = state in ("complete", "partial") and not (result.record and result.record.error_kind) and not failed_notes
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        ttl = max(1, int((midnight - now).total_seconds())) if successful else 300
        value = json.dumps({'rows': [row.model_dump(mode='json') for row in result.rows], 'record': result.record.model_dump(mode='json') if result.record else None, 'errors': result.errors}, ensure_ascii=False)
        self.cache.put(kind, key, 'status', value, ttl)
        if successful:
            self.cache.put(kind, symbol, 'last-success', value, -1)
        return result
