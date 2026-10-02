"""用真实子进程验证服务锁与崩溃恢复，不访问网络或用户数据。"""

import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from radar.calendar import CalendarData, TradingCalendar
from radar.collector_store import CollectorStore


def test_real_daemon_single_instance_and_web_state_rebuild(tmp_path):
    root = Path(__file__).parents[2]
    state = tmp_path / '.stock_robot'
    calendar = TradingCalendar(state / 'radar_collector.db', fetcher=lambda: CalendarData(
        (date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)),
        date(2026, 9, 28), date(2026, 10, 2), datetime.now().astimezone(), 'test',
    ))
    calendar.refresh()
    env = {**os.environ, 'PYTHONPATH': str(root / 'src')}
    command = [sys.executable, '-m', 'stock_robot.cli', 'radar', 'daemon']
    process = subprocess.Popen(command, cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        runtime = {}
        while time.monotonic() < deadline:
            runtime = CollectorStore(state / 'radar_collector.db').runtime()
            if runtime.get('worker_id'):
                break
            assert process.poll() is None, process.communicate(timeout=5)
            time.sleep(.1)
        assert runtime.get('worker_id')
        assert CollectorStore(state / 'radar_collector.db').settings()['enabled'] is False
        duplicate = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20, check=False)
        assert duplicate.returncode != 0
        assert '已有采集服务' in duplicate.stdout + duplicate.stderr
        # 模拟 Web 重建本地依赖，服务进程仍运行且身份保持。
        assert CollectorStore(state / 'radar_collector.db').runtime()['worker_id'] == runtime['worker_id']
        assert process.poll() is None
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)


def test_real_executor_crash_preserves_success_and_recovers_lease(tmp_path):
    root = Path(__file__).parents[2]
    store = CollectorStore(tmp_path / 'collector.db')
    run = store.enqueue('pool', '2026-09-29', 'fingerprint', 'manual', ['ok', 'bad'])
    code = '''
import sys,time
from pathlib import Path
from filelock import FileLock
from radar.collector_store import CollectorStore
store=CollectorStore(Path(sys.argv[1]))
with FileLock(Path(sys.argv[1]).parent/'radar_collector.execute.lock'):
    run=store.claim('crashing-worker',lease_seconds=90)
    store.record_item(run['id'],'ok','completed',worker_id='crashing-worker')
    Path(sys.argv[2]).write_text('claimed')
    time.sleep(60)
'''
    marker = tmp_path / 'claimed'
    process = subprocess.Popen([sys.executable, '-c', code, str(store.db_path), str(marker)],
                               env={**os.environ, 'PYTHONPATH': str(root / 'src')})
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(.05)
        assert marker.exists()
    finally:
        process.kill()
        process.wait(timeout=10)
    current = store.get_run(run['id'])
    assert current is not None
    expired = datetime.fromisoformat(current['lease_until']) + timedelta(seconds=1)
    assert store.recover_expired(expired) == 1
    recovered = store.claim('replacement-worker', expired + timedelta(minutes=2))
    assert recovered is not None
    assert recovered['attempt'] == 2
    assert recovered['items'][0]['status'] == 'completed'
    assert recovered['items'][1]['status'] == 'pending'
