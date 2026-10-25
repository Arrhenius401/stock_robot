"""舆情窗口、稳定标识和标注校验；不联网、不调用模型。"""
import hashlib
import html
import json
import re
from datetime import timedelta
from typing import Any

from data.hk_news import HK_TZ, current_news
from data.schemas import AnalysisContext, SentimentItem

RISK_BY_SEVERITY = {"minor": 2.0, "moderate": 5.0, "major": 10.0}


def assessment_inputs(ctx: AnalysisContext) -> list[dict[str, Any]]:
    """按报告日期过滤真实条目，稳定标识不随模型输出改变。"""
    now = ctx.collected_at.astimezone(HK_TZ)
    start = now.date() - timedelta(days=29)
    candidates: list[dict[str, Any]] = []
    if ctx.market == "hk":
        snapshot = ctx.news_data.hk_news if ctx.news_data else None
        if snapshot is not None:
            for item in current_news(snapshot, now).items:
                candidates.append({"title": item.title, "source": "news", "content": item.content,
                    "publish_date": item.published_at.astimezone(HK_TZ).date().isoformat(),
                    "published_at": item.published_at.astimezone(HK_TZ).isoformat(),
                    "media": item.links[0].media, "url": item.links[0].url,
                    "links": [link.model_dump() for link in item.links]})
    elif ctx.raw_sentiment is not None:
        for item in ctx.raw_sentiment.items:
            if item.title.strip() and start <= item.publish_date <= now.date():
                candidates.append(item.model_dump(mode="json"))
    candidates.sort(key=lambda item: item["publish_date"], reverse=True)
    selected: list[dict[str, Any]] = []
    seen_titles: set[tuple[str, str]] = set()
    seen_urls: set[str] = set()
    for item in candidates:
        key = (item["publish_date"], item["title"].strip())
        if key in seen_titles or (item["url"] and item["url"] in seen_urls):
            continue
        seen_titles.add(key)
        if item["url"]:
            seen_urls.add(item["url"])
        identity = json.dumps([item["source"], *key, item["url"]], ensure_ascii=False)
        item["item_id"] = hashlib.sha256(identity.encode()).hexdigest()[:20]
        selected.append(item)
        if len(selected) >= (20 if ctx.market == "hk" else 30):
            break
    return selected


def collection_failure(ctx: AnalysisContext) -> str:
    """部分失败或过期缓存不冒充完整评估。"""
    record = ctx.collection_records.get("news")
    if record and (record.error_kind or record.error_message or record.cache_status == "stale"):
        return "舆情采集失败或不完整，暂不计分"
    if ((ctx.raw_sentiment and ctx.raw_sentiment._from_stale_cache)
            or (ctx.news_data and ctx.news_data._from_stale_cache)):
        return "实时舆情采集失败，缓存仅作参考"
    snapshot = ctx.news_data.hk_news if ctx.news_data else None
    if snapshot and snapshot.query_errors:
        return "部分关键词采集失败或未执行，暂不计分"
    return ""


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", value))).strip()


def validate_annotations(response: str, inputs: list[dict[str, Any]]) -> list[SentimentItem]:
    """整批逐ID核对，缺项、重复、伪造或无原文依据均使批次失败。"""
    text = response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[-1].strip() != "```":
            raise ValueError("标注JSON围栏不完整")
        text = "\n".join(lines[1:-1]).strip()
    rows = json.loads(text)
    if not isinstance(rows, list) or len(rows) != len(inputs):
        raise ValueError("标注条数与输入不一致")
    originals = {item["item_id"]: item for item in inputs}
    seen: set[str] = set()
    items: list[SentimentItem] = []
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("标注条目结构无效")
        item_id = row.get("item_id")
        if not isinstance(item_id, str) or item_id not in originals or item_id in seen:
            raise ValueError("标注ID缺失、重复或与输入不符")
        seen.add(item_id)
        original = originals[item_id]
        related, assessable = row.get("related"), row.get("assessable")
        if type(related) is not bool or type(assessable) is not bool:
            raise ValueError("标注相关性或可评估字段无效")
        tendency, severity = row.get("tendency"), row.get("severity")
        if not {"tendency", "severity", "evidence"} <= row.keys():
            raise ValueError("标注必要字段缺失")
        if assessable:
            if tendency not in ("positive", "neutral", "negative") or severity not in RISK_BY_SEVERITY:
                raise ValueError("标注倾向或严重度无效")
        elif tendency is not None or severity is not None:
            raise ValueError("不可评估条目不得伪装成已判断的倾向")
        evidence = row.get("evidence")
        if not isinstance(evidence, str) or (assessable and not evidence.strip()):
            raise ValueError("标注原文依据缺失")
        source_text = _normalized(original["title"] + "\n" + original["content"])
        if evidence.strip() and (not _normalized(evidence) or _normalized(evidence) not in source_text):
            raise ValueError("标注依据不在采集原文中")
        if "title" in row and row["title"] != original["title"]:
            raise ValueError("模型修改了原始标题")
        items.append(SentimentItem(item_id=item_id, title=original["title"], summary=evidence[:200],
            related=related, assessable=assessable, tendency=tendency or "", severity=severity or "",
            evidence=evidence, source=original["source"], publish_date=original["publish_date"],
            url=original["url"]))
    return items
