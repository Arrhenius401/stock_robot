"""对已归档年报批量重解析，保持原始采集资料只读。"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import click

from radar.stocks.admission import _BJ
from radar.stocks.annual_reparse import run_annual_reparse
from stock_robot.dividend_documents import _cutoff
from stock_robot.stock_annual_collect import _packet_importer
from stock_robot.stock_research import _protect_database


@click.command("stock-annual-reparse")
@click.option(
    "--source-workspace",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--workspace", type=click.Path(file_okay=False, path_type=Path), required=True
)
@click.option("--as-of", required=True)
@click.option(
    "--db",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="可选：导入已有公司并整理待审财务事实，不审核或发布",
)
def annual_reparse(
    source_workspace: Path, workspace: Path, as_of: str, db: Path | None
) -> None:
    """不联网重解析已有年报任务，可再次运行接续新增的完整断点。"""
    try:
        cutoff = _cutoff(as_of)
        if cutoff > datetime.now(_BJ).date():
            raise ValueError("研究日期不能晚于北京当前日期")
        source_root, root = source_workspace.resolve(), workspace.resolve()
        if source_root.is_relative_to(root) or root.is_relative_to(source_root):
            raise ValueError("原任务与新研究目录不能相同或互相嵌套")
        if db is not None:
            _protect_database(db, source_workspace / "manifest.json")
            if any(
                Path(str(db.resolve()) + suffix).is_relative_to(directory)
                for directory in (root, source_root)
                for suffix in ("", "-wal", "-shm", "-journal")
            ):
                raise ValueError("数据库及日志不能位于原任务或新研究目录内")
        result = run_annual_reparse(
            source_workspace,
            workspace,
            cutoff,
            on_packet=_packet_importer(db, root, cutoff) if db is not None else None,
        )
    except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = {
        key: value
        for key, value in result.items()
        if key not in {"companies", "items", "packet"}
    }
    click.echo(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
