"""统一黄金研报编排：事实计算不依赖模型，所有入口使用同一结果。"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock, Timeout

from gold.models import GoldCollection, GoldRules
from utils.paths import resource_root

logger = logging.getLogger(__name__)
READING_GUIDE = (
    "国内金条、黄金ETF看国内部分；国际黄金、海外资产看国际部分。"
    "持有1—3年看长期配置建议；几周至3个月看短期交易参考。"
    "先看方向与风险，再看依据及重估条件；具体产品另核对成本。"
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
        if not isinstance(payload.get("long_term"), dict) or not isinstance(payload.get("markets"), dict):
            raise TypeError("黄金报告快照事实结构无效")
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

    def _alert_path(self) -> Path:
        digest = hashlib.sha256(json.dumps(self.rules.model_dump(), sort_keys=True).encode()).hexdigest()[:16]
        return Path(self.config.config_dir) / "gold" / f"alerts-{digest}.json"

    def _read_alerts(self) -> dict[str, Any]:
        try:
            value = json.loads(self._alert_path().read_text(encoding="utf-8"), parse_constant=_reject_constant)
            if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("markets"), dict):
                raise ValueError("警报状态结构无效")
            return {key: state for key, state in value["markets"].items()
                    if key in ("domestic", "international") and isinstance(state, dict)}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("黄金前次警报状态不可用，按未知前态处理：%s", type(exc).__name__)
            return {}

    def _restore_alerts(self, collection: GoldCollection) -> None:
        states = self._read_alerts()
        for key in ("domestic", "international"):
            series = collection.series.get(key)
            state = states.get(key)
            if series is None or series.as_of is None or not isinstance(state, dict):
                continue
            try:
                evaluated = datetime.fromisoformat(state["evaluated_at"])
                state_date = date.fromisoformat(state["as_of"])
                if (evaluated.tzinfo is not None and evaluated <= collection.generated_at
                        and state_date <= series.as_of
                        and type(state["overheated_active"]) is bool):
                    series.metadata["overheated_active"] = state["overheated_active"]
                    series.metadata["alert_state_as_of"] = state["as_of"]
            except (ValueError, TypeError, KeyError) as exc:
                logger.debug("跳过无效黄金警报状态 %s：%s", key, exc)

    def _save_alerts(self, collection: GoldCollection, facts: dict) -> None:
        updates = {}
        for key, market in facts.get("markets", {}).items():
            if not isinstance(market, dict) or market.get("short_term", {}).get("direction") == "无法判断":
                continue
            if not market.get("as_of") or key not in ("domestic", "international"):
                continue
            risk = next((row for row in market.get("risks", []) if row.get("key") == "overheated"), None)
            if risk is not None and type(risk.get("active")) is bool:
                updates[key] = {"overheated_active": risk["active"], "as_of": market["as_of"],
                                "evaluated_at": collection.generated_at.isoformat()}
        if not updates:
            return
        path = self._alert_path()
        try:
            from report.formatter import _atomic_write

            path.parent.mkdir(parents=True, exist_ok=True)
            # 调用方持有覆盖恢复、分析和保存的同一把锁，避免并发读取旧前态。
            states = self._read_alerts()
            for key, update in updates.items():
                previous = states.get(key, {})
                try:
                    evaluated = datetime.fromisoformat(previous["evaluated_at"])
                except (ValueError, TypeError, KeyError):
                    evaluated = None
                if ((evaluated is None or evaluated.tzinfo is None or evaluated <= collection.generated_at)
                        and str(previous.get("as_of", "")) <= update["as_of"]):
                    states[key] = update
            _atomic_write(path, json.dumps({"schema_version": 1, "markets": states},
                                          ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (OSError, ValueError, Timeout) as exc:
            logger.warning("黄金警报状态保存失败：%s", type(exc).__name__)
            collection.notes.append("警报状态未保存；下次无法恢复时按未知前态保守检查解除门槛")

    def run(self, *, refresh_cache: bool = False, no_llm: bool = False,
            now: datetime | None = None, save: bool = True) -> dict[str, Any]:
        if self.collector is None:
            from gold.data import GoldCollector
            self.collector = GoldCollector(config_dir=self.config.config_dir, rules=self.rules)
        collection: GoldCollection = self.collector.collect(now=now, refresh=refresh_cache)
        state_lock = None
        try:
            state_path = self._alert_path()
            state_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_lock = FileLock(str(state_path) + ".lock", timeout=3)
            candidate_lock.acquire()
            state_lock = candidate_lock
        except (OSError, Timeout) as exc:
            logger.warning("黄金警报状态锁不可用：%s", type(exc).__name__)
            collection.notes.append("警报状态锁不可用；按未知前态保守检查，不写回状态")
        try:
            if state_lock is not None:
                self._restore_alerts(collection)
            else:
                for series in collection.series.values():
                    series.metadata.pop("overheated_active", None)
            if self.analyzer is None:
                from gold.analysis import analyze_collection
                facts = analyze_collection(collection, self.rules)
            else:
                facts = self.analyzer(collection, self.rules)
            if state_lock is not None:
                self._save_alerts(collection, facts)
        finally:
            if state_lock is not None:
                state_lock.release()
        # 拷贝并严格验证 JSON；模型不接收可修改的原对象。
        payload = json.loads(json.dumps(facts, ensure_ascii=False, allow_nan=False))
        payload.update({
            "report_type": "gold", "market": "gold", "symbol": "GOLD", "name": "黄金综合研报",
            "generated_at": collection.generated_at.isoformat(),
            "rules_version": self.rules.version, "rules_snapshot": self.rules.model_dump(),
            "reading_guide": READING_GUIDE, "notes": collection.notes,
            "sources": [{"key": key, "name": series.name, "source": series.source,
                         "source_url": series.source_url, "unit": series.unit,
                         "as_of": series.as_of.isoformat() if series.as_of else None,
                         "fetched_at": series.fetched_at.isoformat(),
                         "published_at": series.published_at.isoformat() if series.published_at else None,
                         "status": series.status, "error": series.error, "parser": series.parser,
                         "count": len(series.bars) or len(series.observations), "notes": series.notes,
                         "metadata": series.metadata}
                        for key, series in collection.series.items()],
            "commentary": "", "commentary_status": (
                "disabled" if no_llm or not self.config.get("llm.enabled", True) else "unavailable"
            ),
        })
        if not no_llm and self.config.get("llm.enabled", True) and self.llm is not None:
            try:
                commentary = self.llm.generate(
                    json.dumps(payload, ensure_ascii=False, allow_nan=False),
                    system=("用中文解释提供的黄金研报事实。严格保留方向、两套指引和证据支撑度，"
                            "只解释已有数据和缺项，不改变规则结果，不补造全球需求、事件日期、"
                            "仓位、目标价或胜率；分别解释长期配置与短期技术参考。"),
                )
                if not isinstance(commentary, str) or not commentary.strip():
                    raise ValueError("AI 返回空解读")
                payload["commentary"] = commentary.strip()
                payload["commentary_status"] = "ok"
            except Exception as exc:  # noqa: BLE001 — LLM SDK 边界失败不影响确定性报告
                logger.warning("黄金 AI 解读失败，保留事实报告：%s", type(exc).__name__)
                payload["commentary_status"] = "failed"
        if self.renderer is None:
            from gold.report import render_gold_markdown
            markdown = render_gold_markdown(payload)
        else:
            markdown = self.renderer(payload)
        payload["markdown"] = markdown
        payload["status"] = "ok" if any(
            series.bars and series.status == "ok" for series in collection.series.values()
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
