"""从已审核事实研究共同准入，风格未配置时不发布成员。"""

import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import click

from radar.stocks.admission import RULE_VERSION, evaluate_common_admission
from radar.stocks.cash_special import cash_special_verdict
from radar.stocks.company_profile_admission import merge_common_profile
from radar.stocks.financial_admission import merge_common_financial
from radar.stocks.formal_rules import (
    FORMAL_RULE_VERSION,
    merge_financial_quality,
    merge_governance,
    merge_leadership,
)
from radar.stocks.market_value_admission import merge_common_market_value
from radar.stocks.research_store import StockResearchStore
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_materials import _read
from stock_robot.stock_research import _emit, _protect_database

logger = logging.getLogger(__name__)


def _invalidate_result(result: dict[str, Any], reason: str) -> None:
    """资料变更时失效全部嵌套判断，旧数值仅作诊断而不继续表示已通过。"""
    result.update(common_status="pending_verification", admission_status="pending_verification", evaluation_valid=False)

    def invalidate(node: dict[str, Any]) -> None:
        node["evaluation_valid"] = False
        for key in ("approved_conditions_status", "style_status", "admission_status"):
            if key in node:
                node[key] = "pending_verification"
        if "opportunity_status" in node:
            node["opportunity_status"] = "unavailable"
        for check in node.get("checks", []):
            check.update(outcome="pending_verification", reason=reason)
        for branch in node.get("branches", {}).values():
            invalidate(branch)

    invalidate(result)
    for pool in result.get("pools", {}).values():
        invalidate(pool)


def _unavailable_pools(reason: str) -> dict[str, Any]:
    def pending() -> dict[str, Any]:
        return {"approved_conditions_status": "pending_verification", "style_status": "pending_verification",
                "admission_status": "pending_verification", "opportunity_status": "unavailable", "evaluation_valid": False,
                "unconfigured_rules": [], "checks": [{"code": "evaluation_error", "outcome": "pending_verification",
                                                       "reason": reason, "evidence_ids": [], "role": "information"}]}

    dividend = pending()
    dividend["branches"] = {"dividend": pending(), "value": pending()}
    return {"dividend_value": dividend, "growth_trend": pending()}


def evaluate_store(store: StockResearchStore, as_of: date, *, symbol: str | None = None,
                   market_window: list[date] | None = None, include_styles: bool = False,
                   financial_sources: dict[str, dict[str, Any]] | None = None,
                   market_value_sources: dict[str, dict[str, Any]] | None = None,
                   profile_sources: dict[str, dict[str, Any]] | None = None,
                   industry_sources: dict[str, dict[str, Any]] | None = None,
                   growth_sources: dict[str, dict[str, Any]] | None = None,
                   governance_sources: dict[str, dict[str, Any]] | None = None,
                   leadership_sources: dict[str, dict[str, Any]] | None = None,
                   dividend_sources: dict[str, dict[str, Any]] | None = None,
                   turnover_sources: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """读前后revision校验，不能把资料更新中的混合结果当稳定结论。"""
    style_engine = None
    if include_styles:
        from radar.stocks import style

        style_engine = style
    if symbol is None:
        total = store.summary()["companies"]
        companies = store.list_companies(page_size=max(1, total))["items"]
    else:
        company = store.get_company(symbol)
        if company is None:
            raise ValueError("公司不存在")
        companies = [company]
    results = []
    errors = []
    for listed in companies:
        code = listed["symbol"]
        before = None
        try:
            before = store.get_company(code)
            evidence = store.list_evidence(code)
            common = evaluate_common_admission(code, evidence, as_of, market_window=market_window)
            if financial_sources is not None:
                if code not in financial_sources:
                    raise ValueError('缺少服务器绑定的公司财务来源')
                common = merge_common_financial(common, financial_sources[code], as_of)
            if market_value_sources is not None:
                if code not in market_value_sources:
                    raise ValueError('缺少服务器绑定的公司市值来源')
                common = merge_common_market_value(common, market_value_sources[code], as_of)
            if profile_sources is not None:
                if code not in profile_sources:
                    raise ValueError('缺少服务器绑定的上市资料来源')
                common = merge_common_profile(common, profile_sources[code], as_of)
            if industry_sources is not None:
                if code not in industry_sources:
                    raise ValueError('缺少服务器绑定的行业资料来源')
                common['industry_source'] = industry_sources[code]
            issuer = None
            for check in common.get('checks', []):
                if check.get('code') == 'security' and check.get('outcome') == 'passed':
                    values = check.get('values')
                    issuer = values.get('issuer_type') if isinstance(values, dict) else None
            if governance_sources is not None:
                if code not in governance_sources:
                    raise ValueError('缺少绑定的治理来源')
                common = merge_governance(common, governance_sources[code], as_of)
            if leadership_sources is not None:
                if code not in leadership_sources:
                    raise ValueError('缺少绑定的行业地位来源')
                common = merge_leadership(common, leadership_sources[code], as_of)
            if financial_sources is not None:
                common = merge_financial_quality(common, financial_sources[code], as_of, issuer)
            cash_special = (cash_special_verdict(financial_sources[code], issuer, as_of, code)
                            if financial_sources is not None else None)
            if dividend_sources is not None:
                if code not in dividend_sources:
                    raise ValueError('缺少绑定的分红来源')
                common['dividend_source'] = dividend_sources[code]
            if turnover_sources is not None:
                if code not in turnover_sources:
                    raise ValueError('缺少绑定的成交额来源')
                common['turnover_source'] = turnover_sources[code]
            result = (style_engine.evaluate_stock_styles(code, evidence, as_of, market_window=market_window,
                                                                   common_result=common, issuer_type=issuer,
                                                                   cash_special=cash_special)
                      if style_engine else common)
            result["formal_rule_version"] = FORMAL_RULE_VERSION
            result["evaluation_valid"] = True
            if growth_sources is not None:
                if code not in growth_sources:
                    raise ValueError('缺少绑定的成长研究来源')
                source = growth_sources[code]
                if source.get('symbol') != code or source.get('evaluation_as_of') != as_of.isoformat():
                    raise ValueError('成长研究来源身份或截点不符')
                result['growth_source'] = source
                if style_engine:
                    result['pools']['growth_trend']['growth_research'] = source
            after = store.get_company(code)
            if before is None or after is None or before["revision"] != after["revision"]:
                _invalidate_result(result, "评估期间公司资料已变化，请重新评估")
                result["checks"].append({"code": "source_revision", "outcome": "pending_verification",
                                         "reason": "评估期间公司资料已变化，请重新评估", "evidence_ids": []})
                errors.append({"symbol": code, "reason": "source_revision_changed"})
            result.update(name=listed.get("name", code), source_revision=before["revision"] if before else None)
        except Exception as exc:  # noqa: BLE001 单公司计算隔离，失败保留待核验且记录异常
            logger.warning("共同准入计算失败 %s: %s", code, exc)
            errors.append({"symbol": code, "reason": str(exc)})
            result = {"symbol": code, "name": listed.get("name", code), "source_revision": before["revision"] if before else None,
                      "common_status": "pending_verification", "admission_status": "pending_verification",
                      "formal_screening_enabled": False, "as_of": as_of.isoformat(),
                      "rule_version": RULE_VERSION, "evidence_ids": [],
                      "checks": [{"code": "evaluation_error", "outcome": "pending_verification", "reason": str(exc), "evidence_ids": []}]}
            result["evaluation_valid"] = False
            if style_engine:
                result.update(style_rule_version=style_engine.STYLE_RULE_VERSION, pools=_unavailable_pools(str(exc)))
        results.append(result)
    report = {"as_of": as_of.isoformat(), "rule_version": RULE_VERSION, "formal_rule_version": FORMAL_RULE_VERSION,
            "formal_screening_enabled": False, "members_published": 0,
            "research_only": True, "market_window_role": "research_context_only", "items": results, "errors": errors,
            "summary": {"total": len(results), "common_passed": sum(r["common_status"] == "passed" for r in results),
                        "common_failed": sum(r["common_status"] == "failed" for r in results),
                        "pending_verification": sum(r["common_status"] == "pending_verification" for r in results),
                        "eligible_members": 0}}
    if style_engine:
        report["style_rule_version"] = style_engine.STYLE_RULE_VERSION
    return report


@click.command("stock-admission-evaluate")
@click.option("--db", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--as-of", required=True, help="研究截点，YYYY-MM-DD")
@click.option("--symbol", help="可选单家公司，否则评估全部共享候选")
@click.option("--market-window", "window_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="可选研究市场日历JSON：60日列表或stock-market-window产物，不是任意60条行情")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
def admission_evaluate(db: Path, as_of: str, symbol: str | None, window_path: Path | None, output: Path | None) -> None:
    """计算已确认共同条件；人工确认不能替代数值检查，风格仍待配置。"""
    _run_evaluation(db, as_of, symbol, window_path, output)


@click.command("stock-style-evaluate")
@click.option("--db", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--as-of", required=True, help="研究截点，YYYY-MM-DD")
@click.option("--symbol", help="可选单家公司，否则评估全部共享候选")
@click.option("--market-window", "window_path", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="可选研究市场日历JSON：60日列表或stock-market-window产物，不是任意60条行情")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
def style_evaluate(db: Path, as_of: str, symbol: str | None, window_path: Path | None, output: Path | None) -> None:
    """研究两池已确认风格差异，未确认阈值继续待配置。"""
    _run_evaluation(db, as_of, symbol, window_path, output, include_styles=True)


def _read_market_window(cutoff: date, window_path: Path | None) -> tuple[list[date] | None, dict[str, Any] | None]:
    window = None
    trace = None
    if window_path is not None:
        payload, trace = _read(window_path)
        if isinstance(payload, dict):
            if (payload.get("version") != "stock-market-window-v1" or payload.get("market") != "XSHG"
                    or payload.get("as_of") != cutoff.isoformat() or payload.get("research_only") is not True
                    or payload.get("formal_screening_enabled") is not False):
                raise ValueError("市场日期窗口版本、市场、截点或研究属性不匹配")
            payload = payload.get("days")
        if not isinstance(payload, list) or any(not isinstance(day, str) for day in payload):
            raise TypeError("市场窗口必须是ISO日期字符串列表")
        window = [_cutoff(day) for day in payload]
        if len(window) != 60 or len(set(window)) != 60 or window != sorted(window) or window[-1] > cutoff:
            raise ValueError("市场窗口必须是递增唯一的60个市场交易日且不晚于研究截点")
    return window, trace


def _run_evaluation(db: Path, as_of: str, symbol: str | None, window_path: Path | None, output: Path | None,
                    *, include_styles: bool = False) -> None:
    try:
        _protect_database(db, output, window_path)
        if window_path is not None and output is not None and window_path.resolve() == output.resolve():
            raise ValueError("输出不能覆盖市场日期输入")
        cutoff = _cutoff(as_of)
        window, trace = _read_market_window(cutoff, window_path)
        result = evaluate_store(StockResearchStore(db), cutoff, symbol=symbol, market_window=window, include_styles=include_styles)
        result["market_window_source"] = trace
        _emit(result, db, output)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
