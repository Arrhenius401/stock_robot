"""有预算的官方数据探针；原始响应仅写入项目临时目录。"""
import json
import logging
import math
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader

from data.index_mapping import IndexMapping
from index.valuation_factsheet import parse_factsheet_text

logger = logging.getLogger(__name__)
HISTORY_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
PB_URL = "https://www.csindex.com.cn/csindex-home/data-service/indexValuation"
CNI_HISTORY_URL = "https://hq.cnindex.com.cn/market/market/getIndexDailyDataWithDataFormat"
MAX_BYTES = 20 * 1024 * 1024


def _summary(source: str, payload: Any, symbol: str, provider: str, end: date) -> dict:
    if source == "factsheet":
        return parse_factsheet_text(payload, symbol, provider, end)
    if not isinstance(payload, dict):
        raise TypeError("响应顶层必须为对象")
    data = payload.get("data")
    if source == "pb":
        if not isinstance(data, dict) or not isinstance(data.get("indexValuations"), list):
            raise TypeError("每日PB响应缺少indexValuations列表")
        rows = data["indexValuations"]
        names = {"000015": "红利指数", "000922": "中证红利"}
        selected = [row for row in rows if isinstance(row, dict) and row.get("indexName") == names.get(symbol)]
        return {"row_count": len(rows), "matched_rows": selected,
                "fields": sorted({key for row in selected for key in row})}
    rows = data.get("data", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise TypeError("历史响应缺少记录列表")
    if provider == "cni":
        dates = sorted(str(row[0]) for row in rows if isinstance(row, list) and row)
        fields = ["date", "open", "high", "low", "close"]
    else:
        rows = [row for row in rows if isinstance(row, dict) and row.get("indexCode") == symbol]
        dates = sorted(str(row["tradeDate"]) for row in rows if row.get("tradeDate"))
        fields = sorted({key for row in rows for key in row})
    return {"row_count": len(rows), "first_date": dates[0] if dates else None,
            "last_date": dates[-1] if dates else None, "fields": fields}


def _read_response_bytes(url: str, params: dict | None, timeout: float, deadline: float) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("探针总预算已耗尽")
    # 分别约束连接和读取，持续流式响应也逐块检查总预算。
    stage_timeout = min(timeout, remaining / 2)
    with requests.get(url, params=params, timeout=(stage_timeout, stage_timeout), stream=True) as response:
        response.raise_for_status()
        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=65536):
            if time.monotonic() >= deadline:
                raise TimeoutError("读取响应时总预算耗尽")
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError("响应超过20MiB探针上限")
            chunks.append(chunk)
        return b"".join(chunks)


def _request_worker() -> None:
    """子进程只执行一次读取，以临时文件传递大响应和小型状态。"""
    config = json.loads(sys.stdin.read())
    output = Path(config["output"])
    try:
        raw = _read_response_bytes(config["url"], config["params"], config["timeout"], config["deadline"])
        (output / "response.bin").write_bytes(raw)
        result = {"status": "ok"}
    except (requests.Timeout, TimeoutError) as exc:
        result = {"status": "timeout", "detail": str(exc)}
    except requests.HTTPError as exc:
        result = {"status": "http", "detail": str(exc)}
    except requests.RequestException as exc:
        result = {"status": "network", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 — 网络SDK与子进程边界隔离
        logger.warning("探针读取子进程失败: %s", exc)
        result = {"status": "parse", "detail": str(exc)}
    (output / "status.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def _request_bytes(url: str, params: dict | None, timeout: float, deadline: float,
                   output_dir: Path | None = None) -> bytes:
    """绝对预算由父进程执行；持续滴流或DNS阻塞也会被终止。"""
    if time.monotonic() >= deadline:
        raise TimeoutError("探针总预算已耗尽")
    base = output_dir or Path(__file__).resolve().parents[2] / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="probe-worker-", dir=base) as temporary:
        config = {"url": url, "params": params, "timeout": timeout,
                  "deadline": deadline, "output": temporary}
        environment = os.environ.copy()
        source_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
        process = subprocess.Popen(
            [sys.executable, "-c", "from index.source_probe import _request_worker; _request_worker()"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("子进程启动时总预算耗尽")
            process.communicate(input=json.dumps(config).encode(), timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("官方响应读取超过总预算，已终止读取进程") from exc
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdin is not None:
                process.stdin.close()
        status_path = Path(temporary) / "status.json"
        if process.returncode != 0 or not status_path.exists():
            raise requests.ConnectionError("官方读取子进程退出且未返回有效状态")
        result = json.loads(status_path.read_text(encoding="utf-8"))
        kind = result["status"]
        if kind == "timeout":
            raise requests.Timeout(result["detail"])
        if kind == "http":
            raise requests.HTTPError(result["detail"])
        if kind == "network":
            raise requests.ConnectionError(result["detail"])
        if kind != "ok":
            raise ValueError(result["detail"])
        return (Path(temporary) / "response.bin").read_bytes()


def run_probe(symbol: str, source: str, end: date, output_dir: Path, *, root: Path,
              provider: str = "csi", timeout: float = 12, budget: float = 30,
              retries: int = 0) -> dict:
    """逐来源探测，失败分类不影响其余来源；预算耗尽后不再发起请求。"""
    if source not in {"all", "history", "factsheet", "pb"} or provider not in {"csi", "cni", "sse"}:
        raise ValueError("未知来源或指数提供方")
    if not math.isfinite(timeout) or not math.isfinite(budget) or timeout <= 0 or budget <= 0 or not 0 <= retries <= 2:
        raise ValueError("超时和预算必须为正，重试次数必须为0到2")
    if not (len(symbol) == 6 and symbol.isascii() and symbol.isalnum()):
        raise ValueError("指数代码必须为六位ASCII字母数字")
    output_dir = output_dir.resolve()
    if not output_dir.is_relative_to((root / "tmp").resolve()):
        raise ValueError("探针输出必须位于项目tmp目录")
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    deadline = started + budget
    manifest: dict[str, Any] = {"symbol": symbol, "provider": provider, "requested_date": end.isoformat(),
                                "captured_at": datetime.now(UTC).isoformat(),
                                "budget_seconds": budget, "retries": retries, "sources": []}
    sources = ["history", "factsheet", "pb"] if source == "all" else [source]
    for item in sources:
        record: dict[str, Any] = {"source": item, "attempts": 0}
        manifest["sources"].append(record)
        params = None
        if item == "history":
            start = end.replace(year=end.year - 5) if not (end.month == 2 and end.day == 29) else date(end.year - 5, 2, 28)
            if provider == "cni":
                url = CNI_HISTORY_URL
                params = {"indexCode": symbol, "startDate": start.isoformat(), "endDate": end.isoformat(), "frequency": "day"}
            else:
                url = HISTORY_URL
                params = {"indexCode": symbol, "startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d")}
        elif item == "pb":
            url = PB_URL
        else:
            entry = IndexMapping().lookup(symbol)
            url = entry.source_url if entry else None
            if not url:
                record.update(status="failed", failure="configuration", detail="指数目录缺少单张地址", elapsed_seconds=0)
                continue
        record.update(url=url, params=params)
        source_started = time.monotonic()
        for attempt in range(retries + 1):
            if time.monotonic() >= deadline:
                record.update(status="failed", failure="budget", detail="未发起请求：总预算已耗尽")
                break
            record["attempts"] += 1
            try:
                raw = _request_bytes(url, params, timeout, deadline, output_dir)
                suffix = "pdf" if item == "factsheet" else "json"
                raw_path = output_dir / f"{symbol}-{item}-{attempt}.{suffix}"
                raw_path.write_bytes(raw)
                record["raw_path"] = str(raw_path)
                if item == "factsheet":
                    reader = PdfReader(BytesIO(raw))
                    payload = "\n".join(page.extract_text(extraction_mode="layout") for page in reader.pages)
                    text_path = raw_path.with_suffix(".txt")
                    text_path.write_text(payload, encoding="utf-8")
                    record["text_path"] = str(text_path)
                else:
                    payload = json.loads(raw)
                record.update(status="ok", coverage=_summary(item, payload, symbol, provider, end))
                record.pop("failure", None)
                record.pop("detail", None)
                break
            except (requests.Timeout, TimeoutError) as exc:
                category = "budget" if time.monotonic() >= deadline else "timeout"
                record.update(status="failed", failure=category, detail=str(exc))
            except requests.HTTPError as exc:
                record.update(status="failed", failure="http", detail=str(exc))
            except requests.RequestException as exc:
                record.update(status="failed", failure="network", detail=str(exc))
            except Exception as exc:  # noqa: BLE001 — PDF SDK与探针来源隔离边界
                logger.warning("探针解析失败 %s: %s", item, exc)
                record.update(status="failed", failure="parse", detail=str(exc))
                break
        record["elapsed_seconds"] = round(time.monotonic() - source_started, 3)
    manifest["elapsed_seconds"] = round(time.monotonic() - started, 3)
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return manifest
