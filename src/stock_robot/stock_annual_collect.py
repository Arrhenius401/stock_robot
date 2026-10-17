"""通用年度财务批处理命令，机器建议仅作为待审资料。"""

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import click

from radar.stocks.admission import _BJ
from radar.stocks.annual_batch import run_annual_batch
from radar.stocks.index_scope import build_index_scope
from radar.stocks.research_store import StockResearchStore
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_materials import _read
from stock_robot.stock_financial_facts import prepare_from_store
from stock_robot.stock_research import _protect_database


@click.command("stock-annual-collect")
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--workspace", type=click.Path(file_okay=False, path_type=Path), required=True
)
@click.option("--as-of", required=True)
@click.option(
    "--db",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="可选：导入已有公司并整理待审财务事实，仍不审核或发布",
)
def annual_collect(
    input_path: Path, workspace: Path, as_of: str, db: Path | None
) -> None:
    """收集公司列表或指数范围的三年年报，支持检查点续跑。"""
    try:
        root = workspace.resolve()
        if input_path.resolve().is_relative_to(root):
            raise ValueError("公司输入必须位于批处理工作目录之外")
        cutoff = _cutoff(as_of)
        if cutoff > datetime.now(_BJ).date():
            raise ValueError("研究日期不能晚于北京当前日期")
        payload, _trace = _read(input_path)
        if (
            isinstance(payload, dict)
            and payload.get("version") == "official-index-scope-v1"
        ):
            payload = build_index_scope(payload.get("items", []), cutoff)
        companies = payload.get("companies") if isinstance(payload, dict) else payload
        if not isinstance(companies, list):
            raise TypeError("输入必须是公司列表或含companies的对象")
        if db is not None:
            _protect_database(db, input_path)
            if any(
                Path(str(db.resolve()) + suffix).is_relative_to(root)
                for suffix in ("", "-wal", "-shm", "-journal")
            ):
                raise ValueError("工作目录不能包含数据库或其日志")
        store = None

        def import_packet(packet):
            nonlocal store
            if db is None:
                return None
            if store is None:
                store = StockResearchStore(db)
            for company in packet["companies"]:
                if store.get_company(company["symbol"]) is None:
                    raise ValueError("财务资料只能导入已有公司，先准备公司范围")
            raw = json.dumps(
                packet, ensure_ascii=False, sort_keys=True, allow_nan=False
            ).encode()
            imported = store.import_review_packet(
                packet,
                source_sha256=hashlib.sha256(raw).hexdigest(),
                source_label="年度财务批处理：" + str(root),
                data_as_of=cutoff,
            )
            prepared_count = 0
            for company in packet["companies"]:
                symbol = company["symbol"]
                prepared = prepare_from_store(store, symbol, cutoff)
                if prepared["facts"]:
                    store.add_financial_fact_batch(
                        symbol,
                        prepared["facts"],
                        expected_revision=prepared["source_revision"],
                    )
                    prepared_count += len(prepared["facts"])
            return {**imported, "financial_facts_prepared": prepared_count}

        result = run_annual_batch(
            companies,
            workspace,
            cutoff,
            on_packet=import_packet if db is not None else None,
        )
    except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = {
        key: value
        for key, value in result.items()
        if key not in {"companies", "items", "packet"}
    }
    click.echo(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
