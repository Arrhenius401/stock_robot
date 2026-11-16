import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from radar.stocks.research_store import (
    StockResearchError,
    StockResearchStore,
    StockRevisionConflict,
)

SHA = 'a' * 64
NOW = datetime(2026, 10, 15, tzinfo=UTC)


def make(tmp_path, clock=lambda: NOW):
    return StockResearchStore(tmp_path / 'research.db', clock=clock)


def company(store: StockResearchStore, symbol: str) -> dict[str, Any]:
    value = store.get_company(symbol)
    assert value is not None
    return value


def seed(store, name='公司', cutoff=date(2026, 10, 14), sha=SHA):
    return store.import_companies([{'symbol': '600900', 'name': name}], source_sha256=sha,
                                  source_label='离线候选', data_as_of=cutoff)


@pytest.mark.parametrize('reused', [False, True])
def test_import_transaction_guard_prevents_stale_task_writes_even_on_replay(tmp_path, reused):
    store = make(tmp_path)
    if reused:
        seed(store)
    before = store.summary()
    checked = []
    def guard(connection):
        assert connection.in_transaction
        checked.append(True)
        raise StockRevisionConflict('合成任务已失去租约')
    with pytest.raises(StockRevisionConflict, match='租约'):
        store.import_companies([{'symbol': '600900', 'name': '公司'}], source_sha256=SHA,
            source_label='离线候选', data_as_of=date(2026, 10, 14), transaction_guard=guard)
    assert checked == [True] and store.summary() == before


def test_read_only_store_reads_existing_evidence_and_refuses_writes(tmp_path):
    writer = make(tmp_path)
    seed(writer)
    before = writer.summary()
    reader = StockResearchStore(writer.db_path, clock=lambda: NOW, read_only=True)
    assert reader.summary() == before
    assert reader.get_company('600900') == writer.get_company('600900')
    assert reader.list_evidence('600900') == writer.list_evidence('600900')
    with pytest.raises(StockResearchError, match='只读'):
        reader.import_companies([{'symbol': '000333'}], source_sha256='b'*64, source_label='不能写入', data_as_of=NOW.date())
    assert writer.summary() == before


def test_read_only_missing_database_does_not_create_state_directory(tmp_path):
    missing = tmp_path/'never-created'/'research.db'
    with pytest.raises(sqlite3.OperationalError):
        StockResearchStore(missing, read_only=True)
    assert not missing.parent.exists()


def test_read_only_connections_do_not_allow_sql_writes(tmp_path):
    from contextlib import closing

    writer = make(tmp_path)
    seed(writer)
    reader = StockResearchStore(writer.db_path, read_only=True)
    with closing(reader._connect()) as connection, pytest.raises(sqlite3.OperationalError, match='readonly'):
        connection.execute("UPDATE stocks_companies SET name='不允许写入' WHERE symbol='600900'")
    current = writer.get_company('600900')
    assert current is not None and current['name'] == '公司'


def evidence(store, **overrides):
    args = {'kind': 'financial', 'payload': {'value': None}, 'source_url': 'https://static.cninfo.com.cn/a.pdf',
                'disclosed_on': date(2026, 10, 1), 'content_sha256': SHA,
                'expected_revision': company(store, '600900')['revision']}
    args.update(overrides)
    return store.add_evidence('600900', **args)


def test_idempotency_old_batch_and_unknown(tmp_path):
    store = make(tmp_path)
    seed(store)
    revision = company(store, '600900')['revision']
    assert seed(store)['reused']
    assert company(store, '600900')['revision'] == revision
    seed(store, '旧名', date(2025, 1, 1), 'b' * 64)
    assert company(store, '600900')['name'] == '公司'
    assert store.list_evidence('600900')[0]['payload']['name'] == '公司'
    assert company(store, '600900')['admission_status'] == 'pending_verification'
    assert StockResearchStore(tmp_path / 'research.db', clock=lambda: NOW).summary()['companies'] == 1


def test_atomic_validation_and_json(tmp_path):
    store = make(tmp_path)
    with pytest.raises(StockResearchError):
        store.import_companies([{'symbol': '600900'}, {'symbol': 'INVALID'}], source_sha256=SHA,
                               source_label='候选', data_as_of=date(2026, 1, 1))
    assert store.summary()['companies'] == 0
    with pytest.raises(StockResearchError):
        store.import_companies([{'symbol': '600900'}, {'symbol': '600900'}], source_sha256=SHA,
                               source_label='候选', data_as_of=date(2026, 1, 1))
    seed(store)
    evidence(store, payload={'unknown': None, 'bad': float('nan')})
    assert store.list_evidence('600900')[-1]['payload'] == {'unknown': None, 'bad': 'nan'}


def test_reviews_expiry_and_no_migration(tmp_path):
    current = [NOW]
    store = make(tmp_path, lambda: current[0])
    seed(store)
    row = evidence(store)
    review = store.save_review('600900', row['evidence_id'], expected_revision=company(store, '600900')['revision'],
                              decision='confirmed', reviewer='研究员', reason='核对原文')
    assert review['decision'] == 'confirmed'
    assert store.list_evidence('600900')[-1]['review_status'] == 'confirmed'
    current[0] = NOW + timedelta(days=90)
    assert store.list_evidence('600900')[-1]['review_status'] == 'expired'
    evidence(store, content_sha256='c' * 64)
    assert store.list_evidence('600900')[-1]['review_status'] == 'pending_verification'
    assert company(store, '600900')['admission_status'] == 'pending_verification'
    assert len(store.review_history('600900')) == 1


def test_import_flags_do_not_authorize_and_failed_docs(tmp_path):
    store = make(tmp_path)
    origin = {'source_url': 'https://static.cninfo.com.cn/a.pdf', 'disclosed_on': '2026-10-01',
              'file_sha256': SHA, 'status': 'matched_template'}
    packet = {'companies': [{'symbol': '600900', 'financial_verified': True,
              'proposals': [{'fields': {'amount': '1'}, 'origin': origin}],
              'documents': [{**origin, 'status': 'failed'}]}]}
    store.import_review_packet(packet, source_sha256=SHA, source_label='审核包', data_as_of=date(2026, 10, 14))
    rows = store.list_evidence('600900')
    assert all(row['review_status'] == 'pending_verification' for row in rows)
    for row in rows:
        if row['kind'] == 'document':
            with pytest.raises(StockResearchError):
                store.save_review('600900', row['evidence_id'], expected_revision=company(store, '600900')['revision'],
                                  decision='confirmed', reviewer='审核', reason='标记不授权')
        if row['kind'] == 'dividend_proposal':
            store.save_review('600900', row['evidence_id'], expected_revision=company(store, '600900')['revision'],
                              decision='confirmed', reviewer='审核', reason='仅该事实')


@pytest.mark.parametrize('url', ['http://example.com/a', 'https://localhost/a', 'https://127.0.0.1/a',
                                    'https://user:secret@example.com/a'])
def test_bad_sources(tmp_path, url):
    store = make(tmp_path)
    seed(store)
    with pytest.raises(StockResearchError):
        evidence(store, source_url=url)


def test_cas_two_connections(tmp_path):
    store = make(tmp_path)
    seed(store)
    revision = company(store, '600900')['revision']
    def write(index):
        other = make(tmp_path)
        try:
            evidence(other, expected_revision=revision, content_sha256=str(index) * 64)
            return 'success'
        except StockRevisionConflict:
            return 'conflict'
    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(write, [1, 2])) == ['conflict', 'success']


def test_schema_and_etf_coexist(tmp_path):
    path = tmp_path / 'research.db'
    with sqlite3.connect(path) as connection:
        connection.execute('PRAGMA user_version=27')
        connection.execute('CREATE TABLE etf(value TEXT)')
        connection.execute("INSERT INTO etf VALUES ('保留')")
    make(tmp_path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 27
        assert connection.execute('SELECT value FROM etf').fetchone()[0] == '保留'
        connection.execute('UPDATE stocks_schema_versions SET version=999')
    with pytest.raises(StockResearchError):
        make(tmp_path)


def test_future_dates_search_and_naive_clock(tmp_path):
    store = make(tmp_path)
    with pytest.raises(StockResearchError):
        seed(store, cutoff=date(2027, 1, 1))
    seed(store, name='名字%_')
    assert store.list_companies(search='%_')['total'] == 1
    assert store.list_companies(search='_x')['total'] == 0
    with pytest.raises(StockResearchError):
        evidence(store, disclosed_on=date(2027, 1, 1))
    with pytest.raises(StockResearchError):
        make(tmp_path, lambda: datetime(2026, 1, 1))  # noqa: DTZ001 测试拒绝无时区时钟


def test_batch_rollback_when_sql_fails(tmp_path):
    store = make(tmp_path)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("CREATE TRIGGER fail_second BEFORE INSERT ON stocks_companies WHEN NEW.symbol='603000' BEGIN SELECT RAISE(ABORT,'模拟失败'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.import_companies([{'symbol': '600900'}, {'symbol': '603000'}], source_sha256=SHA,
                               source_label='原子导入', data_as_of=date(2026, 1, 1))
    assert store.summary() == {'companies': 0, 'evidence': 0, 'reviews': 0, 'batches': 0}


def test_cross_company_and_future_review(tmp_path):
    current = [NOW]
    store = make(tmp_path, lambda: current[0])
    store.import_companies([{'symbol': '600900'}, {'symbol': '603000'}], source_sha256=SHA,
                           source_label='两家公司', data_as_of=date(2026, 1, 1))
    row = evidence(store)
    with pytest.raises(StockResearchError):
        store.save_review('603000', row['evidence_id'], expected_revision=1, decision='confirmed', reviewer='审核', reason='不允许跨股')
    store.save_review('600900', row['evidence_id'], expected_revision=2, decision='confirmed', reviewer='审核', reason='仅当前事实')
    current[0] = NOW - timedelta(seconds=1)
    assert store.list_evidence('600900')[-1]['review_status'] == 'pending_verification'
    current[0] = NOW + timedelta(days=89, hours=23, minutes=59)
    assert store.list_evidence('600900')[-1]['review_status'] == 'confirmed'


def test_reject_needs_evidence_append_and_exact_repeat(tmp_path):
    store = make(tmp_path)
    seed(store)
    row = evidence(store)
    revision = company(store, '600900')['revision']
    assert evidence(store)['reused']
    assert company(store, '600900')['revision'] == revision
    for decision in ('rejected', 'needs_evidence'):
        store.save_review('600900', row['evidence_id'], expected_revision=company(store, '600900')['revision'],
                          decision=decision, reviewer='研究员', reason='保留完整历史')
        assert store.list_evidence('600900')[-1]['review_status'] == decision
    assert [row['decision'] for row in store.review_history('600900')] == ['rejected', 'needs_evidence']
    hint = store.list_evidence('600900')[0]
    with pytest.raises(StockResearchError):
        store.save_review('600900', hint['evidence_id'], expected_revision=company(store, '600900')['revision'],
                          decision='confirmed', reviewer='研究员', reason='不能确认原始提示')


@pytest.mark.parametrize('name', [0, [], True, ' '])
def test_invalid_company_names(tmp_path, name):
    store = make(tmp_path)
    with pytest.raises(StockResearchError):
        seed(store, name=name)
    assert store.summary()['batches'] == 0


@pytest.mark.parametrize('origin', [
    {'disclosed_on': '2026-02-30'}, {'disclosed_on': '2027-01-01'}, {'disclosed_on': ['2026-01-01']},
])
def test_bad_packet_origin_rolls_back(tmp_path, origin):
    store = make(tmp_path)
    with pytest.raises(StockResearchError):
        store.import_review_packet({'companies': [{'symbol': '600900', 'documents': [origin]}]},
                                   source_sha256=SHA, source_label='坏资料', data_as_of=date(2026, 1, 1))
    assert store.summary()['companies'] == 0


def test_missing_origin_retained_without_confirmation(tmp_path):
    store = make(tmp_path)
    store.import_review_packet({'companies': [{'symbol': '600900', 'financial_proposals': [
        {'fields': {'value': None}, 'origin': {'status': 'matched_template'}}]}]},
        source_sha256=SHA, source_label='未完整', data_as_of=date(2026, 1, 1))
    row = store.list_evidence('600900')[-1]
    assert row['payload']['fields']['value'] is None
    assert not row['confirmable']
    with pytest.raises(StockResearchError):
        store.save_review('600900', row['evidence_id'], expected_revision=1,
                          decision='confirmed', reviewer='审核员', reason='仍缺原文')


@pytest.mark.parametrize('key', ['proposals', 'financial_proposals', 'documents', 'financial_documents'])
@pytest.mark.parametrize('layer', ['entry', 'origin', 'fields'])
def test_packet_explicit_ownership_must_match(tmp_path, key, layer):
    store = make(tmp_path)
    origin = {'source_url': 'https://static.cninfo.com.cn/a.pdf', 'disclosed_on': '2026-10-01',
              'file_sha256': SHA, 'status': 'matched_template'}
    row: dict[str, Any] = {'fields': {'value': '1'}, 'origin': origin} if key.endswith('proposals') else dict(origin)
    if layer == 'fields':
        if not key.endswith('proposals'):
            return
        row['fields']['symbol'] = '000333'
    elif layer == 'origin' and key.endswith('proposals'):
        row['origin']['symbol'] = '000333'
    else:
        row['symbol'] = '000333'
    packet = {'companies': [{'symbol': '600900', key: [row]}]}
    with pytest.raises(StockResearchError):
        store.import_review_packet(packet, source_sha256=SHA, source_label='错误归属', data_as_of=date(2026, 10, 14))
    assert store.summary() == {'companies': 0, 'evidence': 0, 'reviews': 0, 'batches': 0}


def test_empty_proposal_is_not_confirmable(tmp_path):
    store = make(tmp_path)
    packet = {'companies': [{'symbol': '600900', 'proposals': [{'fields': {}, 'origin': {
        'source_url': 'https://static.cninfo.com.cn/a.pdf', 'disclosed_on': '2026-10-01',
        'file_sha256': SHA, 'status': 'matched_template'}}]}]}
    store.import_review_packet(packet, source_sha256=SHA, source_label='空建议', data_as_of=date(2026, 10, 14))
    assert store.list_evidence('600900')[-1]['confirmable'] is False


def test_huge_pagination_and_revision_are_safe(tmp_path):
    store = make(tmp_path)
    seed(store)
    assert store.list_companies(page=10**100, page_size=10**100)['items'] == []
    assert len(store.list_companies(page_size=10**100)['items']) == 1
    with pytest.raises(StockResearchError):
        evidence(store, expected_revision=10**100)


def test_proposal_identity_does_not_trust_supplied_id(tmp_path):
    store = make(tmp_path)
    row = {'fields': {'amount': '1'}, 'origin': {'source_url': 'https://static.cninfo.com.cn/a.pdf',
           'disclosed_on': '2026-10-01', 'file_sha256': SHA, 'status': 'matched_template'}, 'proposal_id': 'untrusted'}
    packet = {'companies': [{'symbol': '600900', 'proposals': [row]}]}
    store.import_review_packet(packet, source_sha256=SHA, source_label='首次', data_as_of=date(2026, 10, 14))
    row['proposal_id'] = 'different'
    store.import_review_packet(packet, source_sha256='b'*64, source_label='再次', data_as_of=date(2026, 10, 14))
    assert sum(row['kind'] == 'dividend_proposal' for row in store.list_evidence('600900')) == 1


def test_official_302_code_is_valid_candidate_identity(tmp_path):
    store = make(tmp_path)
    result = store.import_companies([{'symbol': '302132', 'name': '中航成飞'}], source_sha256=SHA,
                                    source_label='深交所披露代码样本', data_as_of=date(2026, 10, 14))
    assert result['companies_added'] == 1
    assert company(store, '302132')['admission_status'] == 'pending_verification'


def test_connection_initialization_error_closes_connection(tmp_path, monkeypatch):
    closed = []
    class BrokenConnection:
        def execute(self, *_args):
            raise sqlite3.OperationalError('injected pragma failure')
        def close(self):
            closed.append(True)
    monkeypatch.setattr(sqlite3, 'connect', lambda *_args, **_kwargs: BrokenConnection())
    with pytest.raises(sqlite3.OperationalError, match='pragma failure'):
        make(tmp_path)
    assert closed == [True]


@pytest.mark.parametrize('kind', ['candidates', 'review_packet'])
def test_missing_name_in_newer_batch_keeps_known_name(tmp_path, kind):
    store = make(tmp_path)
    seed(store, name='已知公司名称')
    if kind == 'candidates':
        store.import_companies([{'symbol': '600900'}], source_sha256='b' * 64,
                               source_label='仅补资料', data_as_of=date(2026, 10, 15))
    else:
        store.import_review_packet({'companies': [{'symbol': '600900', 'proposals': []}]}, source_sha256='b' * 64,
                                   source_label='仅补资料', data_as_of=date(2026, 10, 15))
    row = company(store, '600900')
    assert row['name'] == '已知公司名称'
    assert row['data_as_of'] == '2026-10-15'


def test_current_name_is_preferred_to_old_cached_name(tmp_path):
    store = make(tmp_path)
    store.import_companies([{'symbol': '600900', 'name': '旧名', 'current_name': '现名'}], source_sha256=SHA,
                           source_label='名称更新', data_as_of=date(2026, 10, 14))
    assert company(store, '600900')['name'] == '现名'
