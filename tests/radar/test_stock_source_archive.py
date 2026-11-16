from pathlib import Path

import pytest

from radar.stocks.source_archive import collect_documents


class Response:
    def __init__(self, status=200, body=b'%PDF-1.7\nbody', headers=None):
        self.status_code = status
        self.body = body
        self.headers = headers or {}

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        pass


def test_cache_and_duplicates(tmp_path):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return Response()

    docs = [{'symbol': '../escape', 'source_url': 'https://example.com/a'},
            {'symbol': 'second', 'source_url': 'https://example.com/a'}]
    result = collect_documents(docs, tmp_path, allowed_hosts={'example.com'}, http_get=get)
    assert len(calls) == 1
    assert len(result['items']) == 2
    assert result['items'][0]['status'] == 'downloaded'
    file = result['items'][0]['file']
    assert Path(file).name == file
    assert (tmp_path / file).read_bytes().startswith(b'%PDF-')
    result = collect_documents(docs, tmp_path, allowed_hosts={'example.com'}, http_get=get)
    assert len(calls) == 1
    assert result['items'][0]['status'] == 'cached'
    (tmp_path / file).write_bytes(b'corrupt')
    collect_documents(docs, tmp_path, allowed_hosts={'example.com'}, http_get=get)
    assert len(calls) == 2


@pytest.mark.parametrize('url', ['http://example.com/a', 'https://user@example.com/a',
                                'https://127.0.0.1/a', 'https://localhost/a',
                                'https://bad.com/a'])
def test_unsafe_urls(tmp_path, url):
    def get(*args, **kwargs):
        pytest.fail('unsafe request')

    result = collect_documents([{'source_url': url}], tmp_path,
                               allowed_hosts={'example.com', '127.0.0.1', 'localhost'}, http_get=get)
    assert result['items'][0]['status'] == 'failed'


def test_redirect_rejected(tmp_path):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs['allow_redirects'] is False
        return Response(302, headers={'Location': 'https://bad.com/x'})

    result = collect_documents([{'source_url': 'https://example.com/a'}], tmp_path,
                               allowed_hosts={'example.com'}, http_get=get)
    assert result['items'][0]['status'] == 'failed'
    assert len(calls) == 1


def test_429_stops_host(tmp_path):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return Response(429)

    result = collect_documents([{'source_url': f'https://example.com/{i}'} for i in range(6)],
                               tmp_path, allowed_hosts={'example.com'}, http_get=get)
    assert len(calls) == 1
    assert all(item['status'] == 'failed' for item in result['items'])


def test_retry_html_and_partial_failure(tmp_path):
    attempts = {}

    def get(url, **kwargs):
        attempts[url] = attempts.get(url, 0) + 1
        if url.endswith('retry') and attempts[url] < 3:
            return Response(503)
        if url.endswith('html'):
            return Response(body=b'<html>challenge</html>')
        return Response()

    result = collect_documents([{'source_url': f'https://example.com/{name}'}
                                for name in ['retry', 'html']], tmp_path,
                               allowed_hosts={'example.com'}, http_get=get)
    assert attempts['https://example.com/retry'] == 3
    assert [item['status'] for item in result['items']] == ['downloaded', 'failed']


def test_refresh_failure_preserves_old_file(tmp_path):
    docs = [{'source_url': 'https://example.com/a'}]
    first = collect_documents(docs, tmp_path, allowed_hosts={'example.com'},
                              http_get=lambda *a, **k: Response())
    artifact = tmp_path / first['items'][0]['file']
    old = artifact.read_bytes()
    result = collect_documents(docs, tmp_path, allowed_hosts={'example.com'}, refresh=True,
                               http_get=lambda *a, **k: Response(404))
    assert result['items'][0]['status'] == 'failed'
    assert artifact.read_bytes() == old
    assert result['items'][0]['previous_artifact']['sha256']


def test_failed_refresh_cannot_fall_back_to_cached(tmp_path):
    docs = [{'source_url': 'https://example.com/a'}]
    collect_documents(docs, tmp_path, allowed_hosts={'example.com'}, http_get=lambda *a, **k: Response())
    collect_documents(docs, tmp_path, allowed_hosts={'example.com'}, refresh=True,
                      http_get=lambda *a, **k: Response(404))
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return Response(404)

    result = collect_documents(docs, tmp_path, allowed_hosts={'example.com'}, http_get=get)
    assert result['items'][0]['status'] == 'failed'
    assert len(calls) == 1


def test_invalid_input(tmp_path):
    with pytest.raises(ValueError):
        collect_documents([{}], tmp_path, allowed_hosts={'example.com'})


def test_timeout_and_redirect_limit(tmp_path):
    import requests

    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if len(calls) < 3:
            raise requests.Timeout('slow')
        return Response()

    result = collect_documents([{'source_url': 'https://example.com/a'}], tmp_path,
                               allowed_hosts={'example.com'}, http_get=get)
    assert result['items'][0]['status'] == 'downloaded'
    assert len(calls) == 3
    redirects = []

    def redirect(url, **kwargs):
        redirects.append(url)
        return Response(302, headers={'Location': f'/next{len(redirects)}'})

    result = collect_documents([{'source_url': 'https://example.com/b'}], tmp_path,
                               allowed_hosts={'example.com'}, http_get=redirect)
    assert result['items'][0]['status'] == 'failed'
    assert len(redirects) == 6


def test_manifest_cannot_supply_outside_path(tmp_path):
    import hashlib
    import json

    outside = tmp_path.parent / 'outside.pdf'
    outside.write_bytes(b'%PDF-1.7\nbody')
    (tmp_path / 'manifest.json').write_text(json.dumps({'documents': {
        'https://example.com/a': {'file': '../outside.pdf',
                                'sha256': hashlib.sha256(outside.read_bytes()).hexdigest(),
                                'bytes': outside.stat().st_size}}}))
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return Response()

    collect_documents([{'source_url': 'https://example.com/a'}], tmp_path,
                      allowed_hosts={'example.com'}, http_get=get)
    assert len(calls) == 1


def test_stream_timeout_uses_same_retry_budget(tmp_path):
    import requests

    class Interrupted(Response):
        def iter_content(self, chunk_size):
            yield b'%PDF-'
            raise requests.Timeout('stream interrupted')

    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return Interrupted()

    result = collect_documents([{'source_url': 'https://example.com/a'}], tmp_path,
                               allowed_hosts={'example.com'}, http_get=get)
    assert len(calls) == 3
    assert result['items'][0]['status'] == 'failed'
    assert not list(tmp_path.glob('*.pdf'))
    assert not list(tmp_path.glob('.download-*'))


def test_cache_read_error_does_not_abort_batch(tmp_path, monkeypatch):
    import radar.stocks.source_archive as archive

    def broken(*args):
        raise PermissionError('locked')

    monkeypatch.setattr(archive, '_artifact', broken)
    result = collect_documents([{'source_url': f'https://example.com/{i}'} for i in range(2)],
                               tmp_path, allowed_hosts={'example.com'},
                               http_get=lambda *a, **k: Response())
    assert len(result['items']) == 2
    assert all(item['status'] == 'downloaded' for item in result['items'])
    assert all('cache_read_failed' in item for item in result['items'])


def test_response_close_failure_keeps_download_success(tmp_path):
    class BadClose(Response):
        def close(self):
            raise OSError('close failed')

    result = collect_documents([{'source_url': 'https://example.com/a'}], tmp_path,
                               allowed_hosts={'example.com'},
                               http_get=lambda *a, **k: BadClose())
    item = result['items'][0]
    assert item['status'] == 'downloaded'
    assert (tmp_path / item['file']).is_file()


def test_soft_limit_warns_without_deleting_existing_files(tmp_path, monkeypatch):
    import radar.stocks.source_archive as module

    monkeypatch.setattr(module, '_SOFT_LIMIT', 1)
    existing = tmp_path / 'keep.txt'
    existing.write_text('保留缓存', encoding='utf-8')
    result = collect_documents([{'source_url': 'https://example.com/a'}], tmp_path,
                               allowed_hosts={'example.com'}, http_get=lambda *_args, **_kwargs: Response())
    assert result['warnings']
    assert existing.read_text(encoding='utf-8') == '保留缓存'
    assert (tmp_path / result['items'][0]['file']).exists()


def test_cross_source_concurrency_is_bounded_to_two(tmp_path):
    from threading import Barrier, Lock

    barrier = Barrier(2)
    lock = Lock()
    active = peak = 0

    class ConcurrentResponse(Response):
        def iter_content(self, chunk_size):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait(timeout=5)
            yield self.body
            with lock:
                active -= 1

    documents = [{'source_url': f'https://{host}.example.com/a'} for host in ('a', 'b', 'c', 'd')]
    result = collect_documents(documents, tmp_path, allowed_hosts={f'{host}.example.com' for host in ('a', 'b', 'c', 'd')},
                               http_get=lambda *_args, **_kwargs: ConcurrentResponse())
    assert all(item['status'] == 'downloaded' for item in result['items'])
    assert peak == 2


def test_final_redirect_url_survives_cache_resume(tmp_path):
    def get(url, **kwargs):
        if url.endswith('/a'):
            return Response(302, headers={'Location': '/final'})
        return Response()

    documents = [{'source_url': 'https://example.com/a'}]
    first = collect_documents(documents, tmp_path, allowed_hosts={'example.com'}, http_get=get)
    second = collect_documents(documents, tmp_path, allowed_hosts={'example.com'}, http_get=get)
    assert first['items'][0]['final_url'] == second['items'][0]['final_url'] == 'https://example.com/final'