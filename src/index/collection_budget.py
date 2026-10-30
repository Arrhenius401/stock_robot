"""指数采集的可终止进程预算，分阶段保留已取得结果。"""
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from data.collection_state import make_record
from data.schemas import AnalysisTarget, IndexAnalysisContext

logger = logging.getLogger(__name__)


def collect_bounded(target: AnalysisTarget, budget: float = 60, on_progress: Callable[[str, int, int, str], None] | None = None, *, refresh_cache: bool = False) -> IndexAnalysisContext:
    root = Path(__file__).resolve().parents[2] / 'tmp'
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='index-collect-', dir=root) as temporary:
        output = Path(temporary) / 'context.json'
        environment = os.environ.copy()
        environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]) + os.pathsep + environment.get('PYTHONPATH', '')
        log_directory = root / "index-collection-logs"
        log_directory.mkdir(exist_ok=True)
        log_path = log_directory / (Path(temporary).name + ".log")
        log_file = log_path.open("wb")
        process = subprocess.Popen([sys.executable, '-m', 'index.collection_budget'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log_file, env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        timed_out = False
        deadline = time.monotonic() + budget
        payload: bytes | None = json.dumps({'target': target.model_dump(mode='json'), 'output': str(output), 'refresh_cache': refresh_cache}).encode()
        completed = 0
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    process.communicate(payload, timeout=min(0.25, remaining))
                    break
                except subprocess.TimeoutExpired:
                    payload = None
                    if on_progress and output.exists():
                        saved = IndexAnalysisContext.model_validate_json(output.read_text(encoding='utf-8'))
                        count = len(saved.collection_records)
                        if count > completed:
                            completed = count
                            on_progress('collect', count, max(5, count), '已取得指数分阶段数据')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            log_file.close()
            if process.stdin is not None:
                process.stdin.close()
        context = IndexAnalysisContext.model_validate_json(output.read_text(encoding='utf-8')) if output.exists() else IndexAnalysisContext(target=target)
        if timed_out or process.returncode:
            logger.warning("指数采集子进程日志：%s", log_path)
            message = f"指数采集达到{budget:g}秒总预算，已终止请求并保留已完成数据" if timed_out else "指数采集进程异常退出，保留已完成数据"
            logger.warning("%s: %s", target.symbol, message)
            context.risk_flags.append(message)
            expected = ["index_price", "index_valuation"]
            if target.index_style == "strategy":
                expected += ["strategy_data", "benchmark"]
            elif target.index_style != "overseas":
                expected += ["index_capital_flow", "index_sentiment"]
                if target.index_style == "sector":
                    expected += ["benchmark"]
                if target.index_style == "broad":
                    expected += ["index_macro"]
            for kind in expected:
                if kind not in context.collection_records:
                    context.collection_records[kind] = make_record("指数采集", "failed", error_kind="timeout" if timed_out else "fetch_failed", error_message=message + "；本项未完成")
        return context


def main() -> None:
    from index.collector import IndexDataCollector
    config = json.loads(sys.stdin.read())
    output = Path(config['output'])
    collector = IndexDataCollector(isolate_requests=False, cache_requests=True)
    collector.checkpoint_path = output
    context = collector.collect(AnalysisTarget.model_validate(config['target']), refresh_cache=config.get('refresh_cache') is True)
    collector._checkpoint(context)


if __name__ == '__main__':
    main()
