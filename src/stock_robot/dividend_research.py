"""离线批量分红研究命令，不发布股票池成员。"""

import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import click

from radar.stocks.dividend_payout import evaluate_company

EVALUATOR_VERSION = "dividend-payout-v1"


def _digest(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    """在同目录替换完整产物，失败时保留旧文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_batch(records: list[dict[str, Any]], as_of: date, cache_dir: Path | None = None) -> dict[str, Any]:
    """每家公司独立缓存，证据变化或截点变化使旧结果失效。"""
    items = []
    reused = 0
    for record in records:
        try:
            key = _digest({"version": EVALUATOR_VERSION, "as_of": as_of.isoformat(), "record": record})
        except (ValueError, TypeError):
            # 单家公司非有限金额等异常仍由评估器说明，不中断其他候选。
            key = None
        cache_path = cache_dir / f"{key}.json" if cache_dir is not None and key is not None else None
        result = None
        if cache_path is not None and cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if (isinstance(cached, dict) and cached.get("key") == key
                        and isinstance(cached.get("result"), dict)
                        and cached.get("checksum") == _digest(cached["result"])):
                    result = cached["result"]
                    reused += 1
            except (OSError, ValueError, TypeError):
                # 缓存可重建，损坏文件不作为财务判断依据。
                result = None
        if result is None:
            # 非有限原始金额在证据中保留为文本，结论仍待核验，不中断其他公司。
            result = json.loads(json.dumps(evaluate_company(record, as_of), ensure_ascii=False, default=str), parse_constant=str)
            if cache_path is not None:
                _write_json(cache_path, {"key": key, "checksum": _digest(result), "result": result})
        items.append(result)
    complete = sum(item["status"] == "complete" for item in items)
    return {"evaluator_version": EVALUATOR_VERSION, "as_of": as_of.isoformat(), "rule_enabled": False,
            "summary": {"total": len(items), "complete": complete, "pending_verification": len(items) - complete,
                        "meets_reference_condition": sum(item.get("meets_reference_condition") is True for item in items),
                        "cache_reused": reused}, "items": items}


@click.command("dividend-research")
@click.option("--input", "input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True,
              help="证据JSON：companies为任意候选列表")
@click.option("--output", "output_path", type=click.Path(dir_okay=False, path_type=Path), required=True,
              help="研究结果JSON，不写入正式股票池")
@click.option("--as-of", required=True, help="研究截止日，YYYY-MM-DD")
@click.option("--cache-dir", type=click.Path(file_okay=False, path_type=Path), help="可选独立结果缓存，支持续跑")
def dividend_research(input_path: Path, output_path: Path, as_of: str, cache_dir: Path | None) -> None:
    """按实际分红证据计算支付率；缺失为待核验，不启用正式筛选。"""
    try:
        cutoff = date.fromisoformat(as_of)
        if input_path.resolve() == output_path.resolve():
            raise ValueError("输入与输出不能是同一文件")
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        records = payload.get("companies") if isinstance(payload, dict) else None
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise ValueError("companies必须是公司对象列表")
        symbols = [record.get("symbol") for record in records]
        if any(not isinstance(symbol, str) or not symbol for symbol in symbols):
            raise ValueError("每家公司必须提供非空字符串symbol")
        if len(symbols) != len(set(symbols)):
            raise ValueError("公司列表存在重复symbol，请先合并证据")
        result = run_batch(records, cutoff, cache_dir)
        _write_json(output_path, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = result["summary"]
    click.echo(f"研究完成：{summary['total']}家，完整{summary['complete']}家，待核验{summary['pending_verification']}家；正式筛选未启用。")


if __name__ == "__main__":
    dividend_research()
