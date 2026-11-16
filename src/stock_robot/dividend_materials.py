"""批量准备候选线索及官方公告原文，不自动证明财务金额。"""

import hashlib
import json
from pathlib import Path
from typing import Any

import click

from radar.stocks.source_archive import collect_documents
from stock_robot.dividend_research import _write_json

DEFAULT_HOSTS = frozenset({"www.cypc.com.cn", "static.cninfo.com.cn", "www.hkexnews.hk",
                          "static.sse.com.cn", "www.sse.com.cn", "disc.static.szse.cn"})


def _index(rows: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{label}必须是对象列表")
    result = {}
    for row in rows:
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"{label}存在缺少symbol的条目")
        if symbol in result:
            raise ValueError(f"{label}存在重复代码{symbol}")
        result[symbol] = row
    return result


def prepare_candidates(candidates: Any, styles: Any = None, evidence: Any = None) -> dict[str, Any]:
    """旧缓存只转为线索，明确补证后才进入支付率输入字段。"""
    roster = _index(candidates, "候选缓存")
    style_map = _index(styles, "风格缓存") if styles is not None else {}
    evidence_map = _index(evidence, "标准化证据") if evidence is not None else {}
    extra = set(evidence_map) - set(roster)
    if extra:
        raise ValueError(f"标准化证据存在候选列表之外的代码：{', '.join(sorted(extra))}")
    companies = []
    for symbol, row in roster.items():
        style = style_map.get(symbol, {})
        company = {
            "symbol": symbol, "name": row.get("current_name", row.get("name", "")),
            "required_share_classes": [], "profits": [], "coverage": [], "events": [],
            "research_hints": {"annual_profit_rows": row.get("annual", []),
                               "dividend_events": style.get("dividend_events", []),
                               "observed_three_year_dividend": style.get("observed_three_year_implemented_dividend"),
                               "provider_org_type": row.get("org_type"),
                               "needs_verification": ["实际派息金额与基数", "普通股利润口径", "全部股类", "年度事件完整性"]},
        }
        if symbol in evidence_map:
            supplied = evidence_map[symbol]
            for key in ("name", "required_share_classes", "profits", "coverage", "events"):
                if key in supplied:
                    company[key] = supplied[key]
        companies.append(company)
    # 无效原始金额保留为文本线索，不输出JSON非标准NaN。
    return json.loads(json.dumps({"companies": companies, "rule_enabled": False,
                                 "preparation_only": True}, ensure_ascii=False, default=str), parse_constant=str)


def _read(path: Path) -> tuple[Any, dict[str, str]]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8-sig")), {"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}


@click.command("dividend-prepare")
@click.option("--candidates", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--style-cache", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--evidence", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="可选已经规范化的证据JSON")
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), required=True)
def prepare_dividends(candidates: Path, style_cache: Path | None, evidence: Path | None, output: Path) -> None:
    """批量转换旧缓存，保留未核验线索，不按总股本推算派息。"""
    try:
        paths = [path for path in (candidates, style_cache, evidence) if path is not None]
        if any(path.resolve() == output.resolve() for path in paths):
            raise ValueError("输出不能覆盖输入资料")
        roster, trace = _read(candidates)
        sources = [trace]
        styles = normalized = None
        if style_cache is not None:
            styles, trace = _read(style_cache)
            sources.append(trace)
        if evidence is not None:
            payload, trace = _read(evidence)
            normalized = payload.get("companies") if isinstance(payload, dict) else None
            if normalized is None:
                raise ValueError("标准化证据缺少companies")
            sources.append(trace)
        result = prepare_candidates(roster, styles, normalized)
        result["input_sources"] = sources
        _write_json(output, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"资料准备完成：{len(result['companies'])}家公司；旧缓存仅作线索，正式筛选未启用。")


@click.command("dividend-archive")
@click.option("--input", "input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--archive-dir", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--allow-host", multiple=True, help="额外允许的公司官方域名，精确匹配，不接受URL")
@click.option("--refresh", is_flag=True, help="重新下载，失败保留已有原文")
def archive_dividends(input_path: Path, archive_dir: Path, output: Path, allow_host: tuple[str, ...], refresh: bool) -> None:
    """按明确URL批量留档公告，下载成功不等于财务核验通过。"""
    try:
        if input_path.resolve() == output.resolve():
            raise ValueError("输出不能覆盖输入资料")
        archive_root = archive_dir.resolve()
        if archive_root in input_path.resolve().parents or archive_root in output.resolve().parents:
            raise ValueError("输入及汇总输出必须位于原文归档目录之外")
        payload, trace = _read(input_path)
        documents = payload.get("documents") if isinstance(payload, dict) else None
        if not isinstance(documents, list):
            raise TypeError("公告清单必须包含documents列表")
        hosts = set(DEFAULT_HOSTS)
        for host in allow_host:
            if not host or any(char in host for char in "/:@"):
                raise ValueError("allow-host必须是独立域名")
            hosts.add(host.lower())
        result = collect_documents(documents, archive_dir, allowed_hosts=hosts, refresh=refresh)
        result["input_source"] = trace
        result["rule_enabled"] = False
        result["financial_verified"] = False
        _write_json(output, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("公告留档完成；状态见输出JSON，原文尚未自动核验财务金额。")
