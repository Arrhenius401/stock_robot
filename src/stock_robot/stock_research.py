"""股票共享研究资料导入与证据审核入口，不提供入池批准。"""

import json
import sqlite3
from pathlib import Path
from typing import Any

import click

from radar.stocks.research_store import StockResearchStore
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_materials import _read
from stock_robot.dividend_research import _write_json


def _protect_database(db: Path, *paths: Path | None) -> None:
    bases = {db, db.resolve()}
    targets = {Path(str(base) + suffix).resolve() for base in bases for suffix in ("", "-wal", "-shm", "-journal")}
    if any(path is not None and path.resolve() in targets for path in paths):
        raise ValueError("输入或输出不能覆盖SQLite数据库及其日志文件")


def _emit(value: Any, db: Path, output: Path | None = None) -> None:
    _protect_database(db, output)
    if output is None:
        click.echo(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        _write_json(output, value)
        click.echo(f"研究资料已导出：{output}；未发布股票池成员。")


@click.command("stock-records-import")
@click.option("--kind", type=click.Choice(["candidates", "review_packet"]), required=True)
@click.option("--input", "input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--db", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--as-of", required=True, help="资料截点，YYYY-MM-DD")
def import_records(kind: str, input_path: Path, db: Path, as_of: str) -> None:
    """将任意候选或审核资料包留档为待核验记录，输入标志不具备批准权。"""
    try:
        _protect_database(db, input_path)
        cutoff = _cutoff(as_of)
        payload, source = _read(input_path)
        # 非有限供应商金额保留为文字线索，不写入非标准JSON。
        payload = json.loads(json.dumps(payload, ensure_ascii=False), parse_constant=str)
        store = StockResearchStore(db)
        if kind == "candidates":
            companies = payload.get("companies") if isinstance(payload, dict) else payload
            if not isinstance(companies, list) or any(not isinstance(row, dict) for row in companies):
                raise TypeError("companies必须是公司对象列表")
            result = store.import_companies(companies, source_sha256=source["sha256"], source_label=source["path"], data_as_of=cutoff)
        else:
            if not isinstance(payload, dict):
                raise TypeError("审核资料包必须是JSON对象")
            result = store.import_review_packet(payload, source_sha256=source["sha256"], source_label=source["path"], data_as_of=cutoff)
        _emit({**result, "admission_status": "pending_verification", "members_published": 0}, db)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command("stock-records-list")
@click.option("--db", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--search", default="", help="全候选库名称或代码搜索")
@click.option("--page", type=click.IntRange(min=1), default=1)
@click.option("--symbol", help="查看单公司证据和审核历史，与搜索列表分开")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
def list_records(db: Path, search: str, page: int, symbol: str | None, output: Path | None) -> None:
    """查看共享候选、证据或历史，不将资料完整程度当入池资格。"""
    try:
        _protect_database(db, output)
        store = StockResearchStore(db)
        if symbol is None:
            result = store.list_companies(search=search, page=page)
            result["summary"] = store.summary()
        else:
            company = store.get_company(symbol)
            if company is None:
                raise ValueError("公司不存在")
            result = {"company": company, "evidence": store.list_evidence(symbol),
                      "reviews": store.review_history(symbol)}
        _emit(result, db, output)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command("stock-evidence-add")
@click.option("--input", "input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--db", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--expected-revision", type=click.IntRange(min=0), required=True)
def add_evidence(input_path: Path, db: Path, expected_revision: int) -> None:
    """显式补充原文证据，资料仍需单独审核。"""
    try:
        _protect_database(db, input_path)
        payload, _ = _read(input_path)
        if not isinstance(payload, dict):
            raise TypeError("证据输入必须是JSON对象")
        required = {"symbol", "kind", "payload", "source_url", "disclosed_on", "content_sha256"}
        if not required <= payload.keys():
            raise ValueError("证据输入缺少必需字段")
        result = StockResearchStore(db).add_evidence(
            payload["symbol"], kind=payload["kind"], payload=payload["payload"], source_url=payload["source_url"],
            disclosed_on=_cutoff(payload["disclosed_on"]), content_sha256=payload["content_sha256"],
            expected_revision=expected_revision)
        _emit(result, db)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command("stock-evidence-review")
@click.option("--db", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--symbol", required=True)
@click.option("--evidence-id", required=True)
@click.option("--expected-revision", type=click.IntRange(min=0), required=True)
@click.option("--decision", type=click.Choice(["confirmed", "rejected", "needs_evidence"]), required=True)
@click.option("--reviewer", required=True)
@click.option("--reason", required=True)
def review_evidence(db: Path, symbol: str, evidence_id: str, expected_revision: int,
                    decision: str, reviewer: str, reason: str) -> None:
    """保存单条事实核验，不能代替共同准入或风格批准。"""
    try:
        result = StockResearchStore(db).save_review(
            symbol, evidence_id, expected_revision=expected_revision, decision=decision,
            reviewer=reviewer, reason=reason)
        _emit({**result, "members_published": 0}, db)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
