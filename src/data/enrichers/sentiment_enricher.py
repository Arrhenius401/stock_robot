"""舆情充实器 — 条目统计、LLM 批量标注"""
import json
import logging

from data.enricher import DataEnricher
from data.schemas import (
    AnalysisContext,
    DimensionSufficiency,
    EnrichedSentiment,
    SentimentItem,
    SufficiencyLevel,
)

logger = logging.getLogger(__name__)

LLM_BATCH_PROMPT = """你是一位金融舆情分析专家。请对以下股票相关的新闻和公告逐条进行标注。

输入格式：JSON 数组，每项包含 title、source（news/announcement）、content。
输出格式：JSON 数组，每项包含：
- title: 原标题
- summary: 一句话摘要（20字以内）
- tendency: "positive" / "neutral" / "negative"
- severity: "minor" / "moderate" / "major"
- event_type: "业绩" / "减持" / "回购" / "监管" / "并购" / "分红" / "其他"

标注原则：
1. 业绩预增、回购、分红 → positive
2. 业绩预降、减持、监管问询、诉讼 → negative
3. 定期报告披露、人事变动 → neutral
4. 涉及重大金额（>1亿）、监管处罚 → major
5. 行业政策、分析师研报 → minor/moderate
6. 无法判断倾向的统一标注 neutral

输入数据：
{items_json}

请只输出 JSON 数组，不要有其他内容。"""


class SentimentEnricher(DataEnricher):
    def __init__(self, llm=None):
        self._llm = llm

    def set_llm(self, llm):
        self._llm = llm

    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        raw = ctx.raw_sentiment
        if raw is None:
            ctx.sufficiency.sentiment = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT, reason="舆情数据完全缺失",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        count = len(raw.items)

        if count >= 10:
            level = SufficiencyLevel.SUFFICIENT
            weight = 1.0
            reason = f"有效新闻公告 {count} 条（≥10），舆情分析完整"
        elif count >= 3:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"有效新闻公告 {count} 条（3–9），情绪参考有限"
        else:
            level = SufficiencyLevel.INSUFFICIENT
            weight = 0.0
            reason = f"有效新闻公告仅 {count} 条（<3），舆情分析不可用"

        enriched = EnrichedSentiment(total_count=count)
        if self._llm and count >= 3:
            try:
                items_json = json.dumps(
                    [{"title": item.title, "source": item.source, "content": item.content[:200]}
                     for item in raw.items],
                    ensure_ascii=False,
                )
                prompt = LLM_BATCH_PROMPT.format(items_json=items_json)
                response = self._llm.generate(prompt)
                response = response.strip()
                # LLM 降级文案（"（LLM 分析暂时不可用"）或空响应：跳过标注，避免 json.loads 噪音
                if not response or response.startswith("（LLM"):
                    logger.warning("LLM 返回降级文案或空响应，跳过舆情标注")
                    items_data = []
                elif response.startswith("```"):
                    # 尝试提取 JSON：处理 ```json / ``` 两种 fence 形式
                    lines = response.split("\n")
                    # 去掉第一行（可能是 ``` 或 ```json）
                    response = "\n".join(lines[1:])
                    response = response.removesuffix("```")
                    response = response.strip()
                    items_data = json.loads(response)
                else:
                    items_data = json.loads(response)
                # 构建标题→来源映射，用于回传 source 到标注结果
                title_to_source = {item.title: item.source for item in raw.items}
                for item_data in items_data:
                    title = item_data.get("title", "")
                    si = SentimentItem(
                        title=title,
                        summary=item_data.get("summary", ""),
                        tendency=item_data.get("tendency", "neutral"),
                        severity=item_data.get("severity", "minor"),
                        event_type=item_data.get("event_type", "其他"),
                        source=title_to_source.get(title, ""),
                    )
                    enriched.all_items.append(si)
                    if si.tendency == "positive":
                        enriched.positive_count += 1
                    elif si.tendency == "negative":
                        enriched.negative_count += 1
                    else:
                        enriched.neutral_count += 1
                    if si.severity == "major":
                        enriched.major_events.append(si)
            except Exception as e:  # noqa: BLE001 — LLM 标注失败不阻断舆情分析
                logger.warning(f"LLM 舆情标注失败: {e}")

        ctx.enriched_sentiment = enriched

        ctx.sufficiency.sentiment = DimensionSufficiency(
            level=level, reason=reason, sample_count=count, score_weight=weight,
        )
        return ctx
