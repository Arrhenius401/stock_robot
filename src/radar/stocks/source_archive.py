"""归档公告原文；下载状态不代表财务证据已核实。"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

logger = logging.getLogger(__name__)
MANIFEST_FILENAME = 'manifest.json'
_SOFT_LIMIT = 1024 ** 3


class _StreamFailure(RuntimeError):
    """流式读取中断，按相同请求预算重试。"""


def _close(response: Any) -> None:
    try:
        response.close()
    # 第三方响应资源清理属于 SDK 隔离边界，不能改变已完成下载状态。
    except Exception as exc:  # noqa: BLE001 第三方响应清理边界，异常记录后不推翻已下载工件
        logger.warning('公告响应关闭失败: %s', exc)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _host(url: str, allowed_hosts: set[str]) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None:
        raise ValueError('只允许无身份信息的 HTTPS 公告 URL')
    if not host or host == 'localhost' or host.endswith('.localhost'):
        raise ValueError('禁止本地地址')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        logger.debug('公告地址使用域名，继续白名单校验')
    else:
        raise ValueError('禁止 IP 地址')
    if host not in allowed_hosts or parsed.port not in (None, 443):
        raise ValueError('公告主机不在允许范围内')
    return host


def _filename(url: str) -> str:
    return hashlib.sha256(url.encode('utf-8')).hexdigest() + '.pdf'


def _artifact(root: Path, url: str, record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    path = root / _filename(url)
    if path.is_symlink() or not path.is_file():
        return None
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as handle:
        if handle.read(5) != b'%PDF-':
            return None
        handle.seek(0)
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
            size += len(chunk)
    if digest.hexdigest() != record.get('sha256') or size != record.get('bytes'):
        return None
    return {'file': path.name, 'sha256': digest.hexdigest(), 'bytes': size,
            'fetched_at': record.get('fetched_at'), 'final_url': record.get('final_url', url)}


def _save(path: Path, value: dict[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(prefix='.manifest-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def collect_documents(documents: list[dict[str, Any]], archive_dir: Path, *,
                      allowed_hosts: set[str], refresh: bool = False,
                      http_get: Callable[..., Any] | None = None) -> dict[str, Any]:
    """批量归档允许来源的 PDF，逐项保存断点，不提取财务字段。"""
    if not isinstance(documents, list) or any(
        not isinstance(doc, dict) or not isinstance(doc.get('source_url'), str)
        or not doc['source_url'].strip() for doc in documents
    ):
        raise ValueError('documents 必须是含非空 source_url 的对象列表')
    if not isinstance(allowed_hosts, set) or not allowed_hosts or any(
        not isinstance(host, str) or not host for host in allowed_hosts
    ):
        raise ValueError('allowed_hosts 必须是非空主机集合')
    allowed = {host.lower() for host in allowed_hosts}
    root = Path(archive_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / MANIFEST_FILENAME
    if manifest_path.is_symlink():
        raise ValueError('归档清单不能是符号链接')
    manifest: dict[str, Any] = {'version': 1, 'documents': {}}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding='utf-8'))
            if isinstance(existing, dict) and isinstance(existing.get('documents'), dict):
                manifest = existing
        except (ValueError, OSError):
            logger.warning('归档清单不可读取，将重建清单', exc_info=True)
    records = manifest['documents']
    get = http_get or requests.get
    blocked: set[str] = set()
    state_lock = Lock()
    # 同一来源串行检查限流，跨来源最多两个任务并行。
    source_lock = Lock()
    host_locks: dict[str, Any] = {}

    def fetch(url: str, failures: int = 0) -> dict[str, Any]:
        result: dict[str, Any] = {'source_url': url, 'status': 'failed', 'attempted_at': _now()}
        previous = None
        try:
            previous = _artifact(root, url, records.get(url))
        except OSError as exc:
            logger.warning('公告缓存不可读取 %s: %s', url, exc)
            result['cache_read_failed'] = str(exc)
        try:
            _host(url, allowed)
            # 最新一次刷新失败的旧工件只用于诊断，不能让后续非刷新任务回退为 cached。
            if previous and not refresh and records.get(url, {}).get('status') != 'failed':
                return {**result, **previous, 'status': 'cached'}
            current = url
            for hop in range(6):
                host = _host(current, allowed)
                with source_lock:
                    lock = host_locks.setdefault(host, Lock())
                response = None
                with lock:
                    with state_lock:
                        if host in blocked:
                            raise ValueError('该来源本轮已因 429 停止')
                    for _attempt in range(3):
                        try:
                            response = get(current, timeout=15, stream=True, allow_redirects=False)
                        except (requests.Timeout, requests.ConnectionError):
                            if failures >= 2:
                                raise
                            failures += 1
                            continue
                        status = response.status_code
                        if status == 429:
                            with state_lock:
                                blocked.add(host)
                            _close(response)
                            raise ValueError('HTTP 429：该来源本轮停止')
                        if status >= 500 and failures < 2:
                            failures += 1
                            _close(response)
                            continue
                        break
                    if response is None:
                        raise ValueError('未取得响应')
                    try:
                        status = response.status_code
                        if status in (301, 302, 303, 307, 308):
                            location = response.headers.get('Location')
                            if not location or hop == 5:
                                raise ValueError('重定向缺少目标或超过五跳')
                            current = urljoin(current, location)
                            _host(current, allowed)
                            continue
                        if status != 200:
                            raise ValueError(f'HTTP {status}')
                        descriptor, name = tempfile.mkstemp(prefix='.download-', dir=root)
                        try:
                            digest = hashlib.sha256()
                            size = 0
                            head = b''
                            with os.fdopen(descriptor, 'wb') as handle:
                                for chunk in response.iter_content(chunk_size=65536):
                                    if not chunk:
                                        continue
                                    head = (head + chunk)[:5]
                                    handle.write(chunk)
                                    digest.update(chunk)
                                    size += len(chunk)
                                handle.flush()
                                os.fsync(handle.fileno())
                            if head != b'%PDF-':
                                raise ValueError('响应不是 PDF，禁止绕过 HTML 验证页面')
                            path = root / _filename(url)
                            if path.is_symlink():
                                raise ValueError('目标文件不能是符号链接')
                            os.replace(name, path)
                            return {**result, 'status': 'downloaded', 'file': path.name,
                                    'sha256': digest.hexdigest(), 'bytes': size,
                                    'fetched_at': _now(), 'final_url': current}
                        except (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError) as exc:
                            raise _StreamFailure(str(exc)) from exc
                        finally:
                            Path(name).unlink(missing_ok=True)
                    finally:
                        _close(response)
            raise ValueError('重定向超过五跳')
        except _StreamFailure as exc:
            if failures < 2:
                return fetch(url, failures + 1)
            logger.warning('公告流式下载重试耗尽 %s: %s', url, exc)
            result['error'] = f'{type(exc).__name__}: {exc}'
            if previous:
                result['previous_artifact'] = previous
            return result
        except Exception as exc:  # noqa: BLE001 第三方下载隔离边界，单项失败不影响批次
            logger.warning('公告归档失败 %s: %s', url, exc)
            result['error'] = f'{type(exc).__name__}: {exc}'
            if previous:
                result['previous_artifact'] = previous
            return result

    results: dict[str, dict[str, Any]] = {}
    urls = list(dict.fromkeys(doc['source_url'] for doc in documents))
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = {executor.submit(fetch, url): url for url in urls}
        for future in as_completed(jobs):
            url = jobs[future]
            result = future.result()
            results[url] = result
            # 刷新失败不丢失旧工件的断点身份，失败本身单独记录。
            records[url] = {**result.get('previous_artifact', {}), **result}
            manifest['updated_at'] = _now()
            _save(manifest_path, manifest)
    items = [{**doc, **results[doc['source_url']]} for doc in documents]
    warnings = []
    total = sum(path.stat().st_size for path in root.iterdir() if path.is_file() and not path.is_symlink())
    if total > _SOFT_LIMIT:
        warnings.append('公告归档超过 1GiB 软上限；仅提醒，不自动删除')
    return {'items': items, 'warnings': warnings, 'archive_bytes': total,
            'manifest_file': MANIFEST_FILENAME}
