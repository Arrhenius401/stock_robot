"""股票池共享证据与筛选闭环的 CLI 入口。

页面由前端负责；这里保证证据准备、复算、计划、补全和快照写入都能在命令行完整执行。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import click

from radar.stocks.completion_apply import load_plan
from radar.stocks.peer_universe import (
    build_peer_universe,
    industry_mapping_sha256,
    now_text,
    read_scope_artifact,
    save_peer_universe,
)
from radar.stocks.screening_pipeline import ScreeningPipeline
from radar.stocks.screening_web import project, read_snapshot
from stock_robot.dividend_documents import _cutoff

_DEFAULT_STATE = Path('.stock_robot')


def _today() -> date:
    return datetime.now().astimezone().date()


def _emit(value: Any, output: Path | None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    if output is None:
        click.echo(text)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + '\n', encoding='utf-8')
        click.echo(f'已导出：{output}')


def _state(state_dir: Path) -> Path:
    return Path(state_dir).absolute()


def _progress(done: int, total: int, items: Any = None, phase: str = 'collect') -> None:
    if done == total or done == 1 or done % 25 == 0:
        click.echo(f'[{phase}] {done}/{total}', err=True)


@click.command('stock-peer-universe-prepare')
@click.option('--state-dir', type=click.Path(file_okay=False, path_type=Path), default=_DEFAULT_STATE, show_default=True)
@click.option('--source-state-dir', type=click.Path(exists=True, file_okay=False, path_type=Path), required=True,
              help='提供完整指数候选范围的只读状态目录')
@click.option('--source-run', required=True, help='该状态中已完成的 discover_candidates 任务标识')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path),
              help='默认写入 <state>/stock-leadership/peer-universe.json')
def peer_universe_prepare(state_dir: Path, source_state_dir: Path, source_run: str, output: Path | None) -> None:
    """从完整指数范围派生同业范围；成员不齐或来源不完整时拒绝生成。"""
    from utils.paths import industry_mapping_path

    state = _state(state_dir)
    try:
        document, artifact_sha = read_scope_artifact(Path(source_state_dir) / 'radar.db', source_run)
        payload = build_peer_universe(document, source_run_id=source_run, source_scope_sha256=artifact_sha,
                                      industry_mapping_sha256=industry_mapping_sha256(industry_mapping_path()),
                                      created_at=now_text())
        target = output or state / 'stock-leadership' / 'peer-universe.json'
        save_peer_universe(target, payload)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(f'同业范围准备失败：{exc}') from exc
    click.echo(f'同业范围已写入：{target}')
    _emit({key: payload[key] for key in ('version', 'universe', 'as_of', 'company_count',
                                         'source_scope_sha256', 'scope_sha256', 'universe_sha256')}, None)


@click.command('stock-evidence-prepare')
@click.option('--state-dir', type=click.Path(file_okay=False, path_type=Path), default=_DEFAULT_STATE, show_default=True)
@click.option('--as-of', default=None, help='资料截点，默认今天（北京时间）')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def evidence_prepare(state_dir: Path, as_of: str | None, output: Path | None) -> None:
    """准备当日共享证据：监管快照 → 同业市值覆盖 → 同业排名。"""
    state = _state(state_dir)
    cutoff = _cutoff(as_of) if as_of else _today()
    try:
        result = ScreeningPipeline(state).prepare_shared(cutoff, _progress)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(f'共享证据准备失败：{exc}') from exc
    _emit(result, output)
    if not (result.get('governance') or {}).get('current') or not (result.get('industry_scale') or {}).get('current'):
        raise click.ClickException('共享证据未完整准备；行业地位或治理条件会保持待检验')


@click.command('stock-screening-update')
@click.option('--state-dir', type=click.Path(file_okay=False, path_type=Path), default=_DEFAULT_STATE, show_default=True)
@click.option('--pool', 'pool_id', required=True)
@click.option('--as-of', default=None, help='资料截点，默认今天（北京时间）')
@click.option('--no-shared-evidence', is_flag=True, help='跳过治理与同业排名，仅采集公司资料（诊断用）')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def screening_update(state_dir: Path, pool_id: str, as_of: str | None, no_shared_evidence: bool,
                     output: Path | None) -> None:
    """按截点更新一个股票池并写入一份完整快照。"""
    state = _state(state_dir)
    cutoff = _cutoff(as_of) if as_of else _today()
    try:
        result = ScreeningPipeline(state).update(pool_id, cutoff, shared=not no_shared_evidence, progress=_progress)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(f'股票池更新失败：{exc}') from exc
    _emit(result, output)
    if result.get('stage_errors'):
        raise click.ClickException('部分资料阶段未完成；筛选结果已保留，可重试或查看阶段错误')


@click.command('stock-screening-plan')
@click.option('--state-dir', type=click.Path(file_okay=False, path_type=Path), default=_DEFAULT_STATE, show_default=True)
@click.option('--pool', 'pool_id', required=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def screening_plan(state_dir: Path, pool_id: str, output: Path | None) -> None:
    """只读生成 v2 补全计划，含共同条件缺项与共享证据阶段。"""
    state = _state(state_dir)
    try:
        plan = ScreeningPipeline(state).plan(pool_id)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(f'补全计划生成失败：{exc}') from exc
    _emit(plan, output)


@click.command('stock-screening-complete')
@click.option('--state-dir', type=click.Path(file_okay=False, path_type=Path), default=_DEFAULT_STATE, show_default=True)
@click.option('--pool', 'pool_id', required=True)
@click.option('--plan', 'plan_path', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--yes', is_flag=True, help='确认执行；与 --dry-run 二选一')
@click.option('--dry-run', is_flag=True, help='只回放可执行项，不读取来源')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def screening_complete(state_dir: Path, pool_id: str, plan_path: Path, yes: bool, dry_run: bool,
                       output: Path | None) -> None:
    """按计划执行公司级补全并写回新快照；共享阶段不在本命令内执行。"""
    if not yes and not dry_run:
        raise click.ClickException('需要 --yes 或 --dry-run')
    state = _state(state_dir)
    try:
        plan = load_plan(plan_path)
        result = ScreeningPipeline(state).complete(pool_id, plan, yes=yes and not dry_run, progress=_progress)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise click.ClickException(f'补全执行失败：{exc}') from exc
    _emit(result, output)


@click.command('stock-screening-close-loop')
@click.option('--state-dir', type=click.Path(file_okay=False, path_type=Path), default=_DEFAULT_STATE, show_default=True)
@click.option('--pool', 'pool_id', required=True)
@click.option('--as-of', default=None, help='资料截点，默认今天（北京时间）')
@click.option('--yes', is_flag=True, help='确认执行共享证据与补全')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def screening_close_loop(state_dir: Path, pool_id: str, as_of: str | None, yes: bool, output: Path | None) -> None:
    """一次完成：共享证据 → 全池复算 → 计划 → 风格补全 → 单次落盘。"""
    if not yes:
        raise click.ClickException('需要 --yes 确认执行共享证据采集与补全')
    state = _state(state_dir)
    cutoff = _cutoff(as_of) if as_of else _today()
    try:
        result = ScreeningPipeline(state).close_loop(pool_id, cutoff, _progress)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(f'闭环执行失败：{exc}') from exc
    _emit(result, output)


@click.command('stock-screening-show')
@click.option('--state-dir', type=click.Path(file_okay=False, path_type=Path), default=_DEFAULT_STATE, show_default=True)
@click.option('--pool', 'pool_id', required=True)
@click.option('--status', type=click.Choice(['passed', 'pending', 'failed', 'all']), default='all', show_default=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def screening_show(state_dir: Path, pool_id: str, status: str, output: Path | None) -> None:
    """查看当前快照的计数与逐家结论，供命令行验收核对。"""
    state = _state(state_dir)
    snapshot = read_snapshot(state, pool_id)
    if snapshot is None:
        raise click.ClickException('当前池没有筛选快照，请先执行 stock-screening-update')
    rows = project(snapshot['report'], pool_id)
    counts = {'screened': len(rows), **{name: sum(row['status'] == name for row in rows)
                                        for name in ('passed', 'pending', 'failed')}}
    selected = [{'symbol': row['symbol'], 'name': row['name'], 'status': row['status'],
                 'industry': row.get('industry'), 'reason': row.get('reason')}
                for row in rows if status == 'all' or row['status'] == status]
    _emit({'pool_id': pool_id, 'as_of': snapshot['as_of'], 'rule_version': snapshot.get('rule_version'),
           'evidence': snapshot.get('evidence'), 'counts': counts, 'items': selected}, output)
