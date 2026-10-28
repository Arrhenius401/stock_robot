"""统一黄金研报编排：事实计算不依赖模型，所有入口使用同一结果。"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from gold.models import GoldCollection, GoldRules
from utils.paths import resource_root

logger = logging.getLogger(__name__)
READING_GUIDE = (
    "国内金条、黄金ETF看国内部分；国际黄金、海外资产看国际部分。"
    "先看价格与行情日期，再参考技术状态、风险和完整依据。"
)
_ALIAS = r"(?:黄金价格|国内金价|国际金价|金价|黄金|国内黄金|国际黄金|伦敦金|人民币金价|gold|au99\.99|au9999|xau|xauusd|xau/usd)"
_GOLD_REQUEST = re.compile(
    r"^(?:请|帮我|帮忙|分析一下|分析|看看|看一下|查询|查看|生成|给我|做一份|今天|今日|最新)*"
    + _ALIAS
    + r"(?:分析报告|分析研报|报告|研报|的走势|走势|未来走势|值得买吗|现在能买吗|现在值得买吗|怎么样|怎么看|分析)?$",
    re.IGNORECASE,
)


def is_gold_request(value: str) -> bool:
    """明确黄金别名才走直达入口，黄金股票/ETF/首饰仍走原有处理。"""
    normalized = re.sub(r"[\s？?，,。！!：:]", "", str(value).strip())
    return bool(_GOLD_REQUEST.fullmatch(normalized))


def load_rules(config: Any = None) -> GoldRules:
    path = resource_root() / "config" / "gold.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        raise TypeError("黄金规则配置必须是对象")
    overrides = config.get("gold", {}) if config is not None else {}
    if overrides:
        if not isinstance(overrides, dict):
            raise ValueError("gold 配置必须是对象")
        data.update(overrides)
    return GoldRules.model_validate(data)


def _reject_constant(value: str) -> None:
    raise ValueError(f"快照含非有限值 {value}")


def load_gold_snapshot(path: Path, markdown: str) -> dict[str, Any] | None:
    """快照必须匹配当前正文；损坏时仅回退原 Markdown。"""
    try:
        data = json.loads(path.with_suffix(".report.json").read_text(encoding="utf-8"),
                          parse_constant=_reject_constant)
        if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
                or data.get("schema_version") != 1):
            raise ValueError("黄金报告快照版本无效")
        if data.get("markdown_sha256") != hashlib.sha256(markdown.encode("utf-8")).hexdigest():
            raise ValueError("黄金报告快照与正文不匹配")
        payload = data.get("payload")
        if not isinstance(payload, dict) or payload.get("report_type") != "gold" or payload.get("symbol") != "GOLD":
            raise TypeError("黄金报告快照身份无效")
        schema = payload.get("report_schema")
        if not isinstance(payload.get("markets"), dict):
            raise TypeError("黄金报告快照市场结构无效")
        if schema == "gold-observation-v2":
            if not isinstance(payload.get("observations"), list):
                raise TypeError("黄金报告观察数据无效")
        elif schema is not None or not isinstance(payload.get("long_term"), dict):
            raise TypeError("黄金报告快照版本或事实结构无效")
        json.dumps(payload, allow_nan=False)
        return payload
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("黄金报告快照不可用，回退原文：%s（%s）", path.name, exc)
        return None


class GoldService:
    def __init__(self, config: Any = None, *, collector: Any = None,
                 rules: GoldRules | None = None, llm: Any = None,
                 output_dir: Path | None = None,
                 analyzer: Callable[[GoldCollection, GoldRules], dict[str, Any]] | None = None,
                 renderer: Callable[[dict[str, Any]], str] | None = None):
        if config is None:
            from utils.config import Config
            config = Config()
        self.config = config
        self.rules = rules or load_rules(config)
        self.collector = collector
        self.llm = llm
        self.output_dir = output_dir or Path.cwd() / "reports"
        self.analyzer = analyzer
        self.renderer = renderer

    def run(self, *, refresh_cache: bool = False, no_llm: bool = False,
            now: datetime | None = None, save: bool = True) -> dict[str, Any]:
        if self.collector is None:
            from gold.data import GoldCollector
            self.collector = GoldCollector(config_dir=self.config.config_dir, rules=self.rules)
        collection: GoldCollection = self.collector.collect(now=now, refresh=refresh_cache)
        for series in collection.series.values():
            series.metadata.pop("overheated_active", None)
            series.metadata.pop("alert_state_as_of", None)
        if self.analyzer is None:
            from gold.analysis import analyze_collection
            facts = analyze_collection(collection, self.rules)
        else:
            facts = self.analyzer(collection, self.rules)
        if not isinstance(facts.get("observations"), list) or not isinstance(facts.get("markets"), dict):
            raise TypeError("黄金观察数据结构无效")
        # 拷贝并严格验证 JSON；模型不接收可修改的原对象。
        payload = json.loads(json.dumps(facts, ensure_ascii=False, allow_nan=False))
        payload.update({
            "report_type": "gold", "report_schema": "gold-observation-v2",
            "market": "gold", "symbol": "GOLD", "name": "黄金市场观察简报",
            "generated_at": collection.generated_at.isoformat(),
            "rules_version": self.rules.version, "rules_snapshot": self.rules.model_dump(
                exclude={"core_weight", "reference_weight", "rsi_entry_min", "rsi_entry_max",
                         "rsi_hot", "rsi_exit", "atr_hot", "atr_exit", "rate_threshold_bp",
                         "dollar_threshold_pct", "holdings_threshold_pct", "rate_marginal_threshold_bp",
                         "dollar_marginal_threshold_pct", "holdings_marginal_threshold_pct",
                         "significant_high_percentile", "significant_low_percentile"}),
            "reading_guide": READING_GUIDE, "notes": collection.notes,
            "sources": [{"key": key, "name": series.name, "source": series.source,
                         "source_url": series.source_url, "unit": series.unit,
                         "as_of": series.as_of.isoformat() if series.as_of else None,
                         "fetched_at": series.fetched_at.isoformat() if series.status != "unavailable" else None,
                         "published_at": series.published_at.isoformat() if series.published_at else None,
                         "status": series.status, "error": series.error, "parser": series.parser,
                         "count": len(series.bars) or len(series.observations), "notes": series.notes,
                         "metadata": series.metadata,
                         "collection_record": series.collection_record.model_dump(mode="json")
                         if series.collection_record else None}
                        for key, series in collection.series.items()],
            "commentary": "", "commentary_status": "not_used",
        })
        if self.renderer is None:
            from gold.report import render_gold_markdown
            markdown = render_gold_markdown(payload)
        else:
            markdown = self.renderer(payload)
        payload["markdown"] = markdown
        payload["status"] = "ok" if all(
            payload["markets"].get(key, {}).get("status") == "ok"
            and payload["markets"].get(key, {}).get("result_state") != "partial"
            for key in ("domestic", "international")
        ) else "partial"
        if save:
            self._save(payload, markdown)
        return payload

    def _save(self, payload: dict[str, Any], markdown: str) -> None:
        from report.formatter import ReportFormatter, _atomic_write
        path = ReportFormatter.save(markdown, "GOLD", output_dir=self.output_dir, category="gold")
        relative = path.relative_to(self.output_dir)
        payload["report_path"] = relative.as_posix()
        payload["report_id"] = base64.urlsafe_b64encode(relative.as_posix().encode("utf-8")).decode("ascii").rstrip("=")
        wrapper = {"schema_version": 1,
                   "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                   "payload": payload}
        try:
            _atomic_write(path.with_suffix(".report.json"),
                          json.dumps(wrapper, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except OSError as exc:
            logger.warning("黄金快照保存失败，已保留 Markdown：%s", exc)
            payload.setdefault("notes", []).append("结构化快照保存失败，报告库仍可查看原文")
