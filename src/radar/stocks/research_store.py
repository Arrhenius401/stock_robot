"""保存共享候选、原文线索及追加式事实审核，不授予股票池资格。"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import math
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing, contextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


class StockResearchError(ValueError):
    """股票研究输入或存储版本无效。"""


class StockRevisionConflict(StockResearchError):
    """公司版本发生变化，调用方须重新读取。"""


def _json(value: Any) -> str:
    def safe(item: Any) -> Any:
        if isinstance(item, float) and not math.isfinite(item):
            return str(item)
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise StockResearchError('JSON对象键必须是字符串')
            return {key: safe(child) for key, child in item.items()}
        if isinstance(item, list):
            return [safe(child) for child in item]
        if item is None or isinstance(item, (str, bool, int, float)):
            return item
        raise StockResearchError('只允许JSON原生值')
    try:
        return json.dumps(safe(value), ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise StockResearchError('无效JSON资料') from exc


def _id(value: Any) -> str:
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StockResearchError(f'{label}不能为空')
    return value.strip()


def _symbol(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r'(?:60[0135]|688|000|001|002|003|30[012])\d{3}', value) is None:
        raise StockResearchError('必须使用沪深A股六位代码')
    return value


def _sha(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise StockResearchError('原文摘要必须为64位小写SHA256')
    return value


def _url(value: Any) -> str:
    value = _text(value, '来源URL')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        port = parsed.port
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or not host or host == 'localhost' or host.endswith('.localhost') or port not in (None, 443)
                or any(char.isspace() for char in value)):
            raise StockResearchError('来源须为无凭据的公开HTTPS域名')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            logger.debug('原文来源使用域名')
        else:
            raise StockResearchError('来源不允许IP地址')
    except ValueError as exc:
        raise StockResearchError('无效来源URL') from exc
    return value


class StockResearchStore:
    """独立专表管理研究资料，同库ETF数据保持原样。"""

    def __init__(self, db_path: Path, *, clock: Callable[[], datetime] | None = None):
        self.db_path = Path(db_path)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._now()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_schema_versions (version INTEGER NOT NULL)')
            versions = connection.execute('SELECT version FROM stocks_schema_versions').fetchall()
            if versions and (len(versions) != 1 or versions[0][0] != 1):
                raise StockResearchError('不支持的股票研究存储版本')
            for statement in (
                'CREATE TABLE IF NOT EXISTS stocks_source_batches (batch_id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_sha256 TEXT NOT NULL, source_label TEXT NOT NULL, data_as_of TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS stocks_companies (symbol TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL, data_as_of TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS stocks_evidence (evidence_id TEXT PRIMARY KEY, symbol TEXT NOT NULL REFERENCES stocks_companies(symbol), kind TEXT NOT NULL, payload TEXT NOT NULL, source_url TEXT, disclosed_on TEXT, content_sha256 TEXT, confirmable INTEGER NOT NULL, created_at TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS stocks_reviews (review_id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL REFERENCES stocks_companies(symbol), evidence_id TEXT NOT NULL REFERENCES stocks_evidence(evidence_id), decision TEXT NOT NULL, reviewer TEXT NOT NULL, reason TEXT NOT NULL, reviewed_at TEXT NOT NULL)',
            ):
                connection.execute(statement)
            if not versions:
                connection.execute('INSERT INTO stocks_schema_versions VALUES (1)')

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA busy_timeout=5000')
            return connection
        except sqlite3.Error:
            connection.close()
            raise

    @contextmanager
    def _write(self):
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            yield connection

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise StockResearchError('clock必须返回带时区时间')
        return now.astimezone(UTC)

    def _date(self, value: Any) -> str:
        if isinstance(value, str):
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise StockResearchError('无效日期') from exc
            if parsed.isoformat() != value:
                raise StockResearchError('日期须为YYYY-MM-DD')
        elif isinstance(value, date) and not isinstance(value, datetime):
            parsed = value
        else:
            raise StockResearchError('日期须为YYYY-MM-DD')
        if parsed > self._now().astimezone(timezone(timedelta(hours=8))).date():
            raise StockResearchError('拒绝未来资料日期')
        return parsed.isoformat()

    def _evidence(self, symbol: str, kind: str, payload: dict[str, Any], origin: dict[str, Any], *,
                  confirmable: bool) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise StockResearchError('证据必须是JSON对象')
        serialized = _json(payload)
        url, disclosed, sha = origin.get('source_url'), origin.get('disclosed_on'), origin.get('file_sha256')
        if any(value is not None and not isinstance(value, str) for value in (url, disclosed, sha)):
            raise StockResearchError('原文追踪必须为字符串或未知值')
        # 不完整原文线索保留原值，显式审核也不能将无追踪资料确认为事实。
        valid = False
        try:
            _url(url)
            _sha(sha)
            self._date(disclosed)
            valid = True
        except StockResearchError:
            valid = False
        if disclosed is not None:
            self._date(disclosed)
        identity_payload = json.loads(serialized)
        if kind in {'dividend_proposal', 'financial_proposal'}:
            identity_payload = {'fields': payload.get('fields'), 'origin': payload.get('origin')}
        identity = {'symbol': symbol, 'kind': kind, 'payload': identity_payload,
                    'source_url': url, 'disclosed_on': disclosed, 'content_sha256': sha}
        return {**identity, 'evidence_id': _id(identity), 'payload': serialized,
                'confirmable': bool(valid and confirmable)}

    @staticmethod
    def _insert_evidence(connection: sqlite3.Connection, row: dict[str, Any], now: str) -> bool:
        cursor = connection.execute('INSERT OR IGNORE INTO stocks_evidence VALUES (?,?,?,?,?,?,?,?,?)',
            (row['evidence_id'], row['symbol'], row['kind'], row['payload'], row['source_url'],
             row['disclosed_on'], row['content_sha256'], int(row['confirmable']), now))
        return cursor.rowcount == 1

    def import_companies(self, records: list[dict[str, Any]], *, source_sha256: str,
                         source_label: str, data_as_of: date) -> dict[str, Any]:
        return self._import(records, source_sha256, source_label, data_as_of, 'candidates')

    def import_review_packet(self, packet: dict[str, Any], *, source_sha256: str,
                             source_label: str, data_as_of: date) -> dict[str, Any]:
        if not isinstance(packet, dict) or not isinstance(packet.get('companies'), list):
            raise StockResearchError('审核包必须包含companies列表')
        return self._import(packet['companies'], source_sha256, source_label, data_as_of, 'review_packet', packet)

    def _import(self, records: list[dict[str, Any]], sha: str, label: str, cutoff: date, kind: str,
                original: dict[str, Any] | None = None) -> dict[str, Any]:
        sha, label, day = _sha(sha), _text(label, '来源名称'), self._date(cutoff)
        if not isinstance(records, list):
            raise StockResearchError('候选必须是列表')
        raw = _json(original if original is not None else records)
        batch_id = _id({'kind': kind, 'data_as_of': day, 'source_sha256': sha})
        prepared = []
        seen: set[str] = set()
        for company in records:
            if not isinstance(company, dict):
                raise StockResearchError('公司必须是对象')
            symbol = _symbol(company.get('symbol'))
            if symbol in seen:
                raise StockResearchError('公司代码重复')
            seen.add(symbol)
            name = company.get('current_name') or company.get('name')
            if name is None:
                name = symbol
            name = _text(name, '公司名称')
            rows = [self._evidence(symbol, 'research_hint', company, {}, confirmable=False)]
            if kind == 'review_packet':
                for key, evidence_kind in (('proposals', 'dividend_proposal'), ('financial_proposals', 'financial_proposal'),
                                           ('documents', 'document'), ('financial_documents', 'financial_document')):
                    entries = company.get(key, [])
                    if not isinstance(entries, list):
                        raise StockResearchError(f'{key}必须是列表')
                    for entry in entries:
                        if not isinstance(entry, dict):
                            raise StockResearchError(f'{key}条目必须是对象')
                        origin = entry.get('origin', {}) if key.endswith('proposals') else entry
                        if not isinstance(origin, dict):
                            raise StockResearchError('原文追踪必须是对象')
                        for ownership in (entry, origin, entry.get('origin'), entry.get('fields')):
                            if isinstance(ownership, dict) and 'symbol' in ownership and ownership['symbol'] != symbol:
                                raise StockResearchError('资料不能跨公司')
                        if key.endswith('proposals'):
                            fields = entry.get('fields')
                            if not isinstance(fields, dict):
                                raise StockResearchError('建议必须包含fields对象')
                            if fields.get('symbol', symbol) != symbol:
                                raise StockResearchError('建议不能跨公司')
                            for field in ('source_url', 'disclosed_on'):
                                if field in fields and fields[field] != origin.get(field):
                                    raise StockResearchError('建议与原文追踪不一致')
                        success = origin.get('status') == 'matched_template'
                        if key.endswith('proposals'):
                            success = success and bool(entry['fields'])
                        rows.append(self._evidence(symbol, evidence_kind, entry, origin, confirmable=success))
                unresolved = company.get('unresolved', [])
                if not isinstance(unresolved, list) or any(not isinstance(item, str) for item in unresolved):
                    raise StockResearchError('未解决事项必须是字符串列表')
                rows.extend(self._evidence(symbol, 'unresolved', {'message': item}, {}, confirmable=False)
                            for item in unresolved)
            prepared.append((symbol, name, rows))
        now = self._now().isoformat()
        added = evidence_added = 0
        with self._write() as connection:
            existing = connection.execute('SELECT payload FROM stocks_source_batches WHERE batch_id=?', (batch_id,)).fetchone()
            if existing:
                if existing['payload'] != raw:
                    raise StockResearchError('同一输入摘要对应不同资料')
                return {'batch_id': batch_id, 'companies_added': 0, 'evidence_added': 0, 'reused': True}
            connection.execute('INSERT INTO stocks_source_batches VALUES (?,?,?,?,?,?,?)',
                               (batch_id, kind, sha, label, day, raw, now))
            for symbol, name, rows in prepared:
                existing = connection.execute('SELECT * FROM stocks_companies WHERE symbol=?', (symbol,)).fetchone()
                if existing is not None and name == symbol:
                    name = existing['name']
                changed = False
                if existing is None:
                    connection.execute('INSERT INTO stocks_companies VALUES (?,?,0,?)', (symbol, name, day))
                    added += 1
                    changed = True
                elif day >= existing['data_as_of'] and (name != existing['name'] or day != existing['data_as_of']):
                    connection.execute('UPDATE stocks_companies SET name=?,data_as_of=? WHERE symbol=?', (name, day, symbol))
                    changed = True
                for row in rows:
                    if self._insert_evidence(connection, row, now):
                        evidence_added += 1
                        changed = True
                if changed:
                    connection.execute('UPDATE stocks_companies SET revision=revision+1 WHERE symbol=?', (symbol,))
        return {'batch_id': batch_id, 'companies_added': added, 'evidence_added': evidence_added, 'reused': False}

    @staticmethod
    def _check_revision(revision: int) -> None:
        if isinstance(revision, bool) or not isinstance(revision, int) or not 0 <= revision <= 2**63 - 2:
            raise StockResearchError('expected_revision必须为可存储的非负整数')

    @staticmethod
    def _revision(connection: sqlite3.Connection, symbol: str, revision: int) -> int:
        StockResearchStore._check_revision(revision)
        row = connection.execute('SELECT revision FROM stocks_companies WHERE symbol=?', (symbol,)).fetchone()
        if row is None:
            raise StockResearchError('公司不存在')
        if row['revision'] != revision:
            raise StockRevisionConflict('公司版本已更新，请重新读取')
        return revision

    def _validate_financial_dependencies(self, connection: sqlite3.Connection, row: dict[str, Any]) -> None:
        payload = row['payload']
        payload = json.loads(payload) if isinstance(payload, str) else payload
        if '_financial_dependencies' not in payload:
            return
        if row['kind'] not in {'admission_profitability', 'style_cash_flow'}:
            raise StockResearchError('财务来源依赖仅用于规范利润与现金流组')
        dependencies = payload['_financial_dependencies']
        if (not isinstance(dependencies, dict) or set(dependencies) != {'version', 'as_of', 'inventory', 'security'}
                or type(dependencies['version']) is not int or dependencies['version'] != 1):
            raise StockResearchError('财务来源依赖版本或结构无效')
        as_of = date.fromisoformat(self._date(dependencies['as_of']))
        inventory = dependencies['inventory']
        if not isinstance(inventory, list) or not inventory:
            raise StockResearchError('财务原文清单不能为空')
        for reference in inventory:
            if not isinstance(reference, dict) or set(reference) != {'evidence_id', 'review_id'}:
                raise StockResearchError('财务原文清单条目无效')
            _sha(reference['evidence_id'])
            review_id = reference['review_id']
            if review_id is not None and (type(review_id) is not int or review_id < 1):
                raise StockResearchError('财务来源审核版本无效')
        # 只重建原始建议和发行人事实，避免派生事实递归依赖自身。
        sources = connection.execute("SELECT * FROM stocks_evidence WHERE symbol=? AND kind IN ('financial_proposal','admission_security') ORDER BY evidence_id", (row['symbol'],)).fetchall()
        expected_inventory = []
        evidence = []
        now = self._now()
        for source in sources:
            review = connection.execute('SELECT * FROM stocks_reviews WHERE evidence_id=? ORDER BY review_id DESC LIMIT 1', (source['evidence_id'],)).fetchone()
            item = dict(source)
            status = 'pending_verification'
            if review:
                try:
                    reviewed = datetime.fromisoformat(review['reviewed_at'])
                    if reviewed.tzinfo is None or reviewed.utcoffset() is None:
                        status = 'needs_evidence'
                    elif reviewed <= now:
                        status = review['decision']
                        if status == 'confirmed' and now - reviewed >= timedelta(days=90):
                            status = 'expired'
                except (ValueError, TypeError, OverflowError):
                    status = 'needs_evidence'
            item.update(payload=json.loads(item['payload']), confirmable=bool(item['confirmable']),
                        latest_review=dict(review) if review else None, review_status=status)
            evidence.append(item)
            if item['kind'] == 'financial_proposal':
                expected_inventory.append({'evidence_id': item['evidence_id'], 'review_id': review['review_id'] if review else None})
        if inventory != expected_inventory:
            raise StockRevisionConflict('财务原文或审核版本已变化，请重新准备事实')
        # 局部导入保持存储基础类型可供纯准备器复用，不在模块初始化时形成循环。
        from radar.stocks.financial_facts import prepare_financial_facts

        prepared = prepare_financial_facts(row['symbol'], evidence, as_of)
        expected = next((fact for fact in prepared['facts'] if fact['kind'] == row['kind']), None)
        actual = {'kind': row['kind'], 'payload': payload, 'source_url': row['source_url'],
                  'disclosed_on': row['disclosed_on'], 'content_sha256': row['content_sha256']}
        if expected is None or _json(actual) != _json(expected):
            raise StockRevisionConflict('规范事实与当前原文的准备结果不一致，请重新准备')

    def add_financial_fact_batch(self, symbol: str, facts: list[dict[str, Any]], *, expected_revision: int) -> dict[str, Any]:
        self._check_revision(expected_revision)
        symbol = _symbol(symbol)
        if not isinstance(facts, list) or not facts:
            raise StockResearchError('没有可保存的规范财务事实')
        prepared = []
        kinds = set()
        for fact in facts:
            if not isinstance(fact, dict) or set(fact) != {'kind', 'payload', 'source_url', 'disclosed_on', 'content_sha256'}:
                raise StockResearchError('规范事实批次字段无效')
            kind = fact['kind']
            if not isinstance(kind, str) or kind not in {'admission_profitability', 'style_cash_flow'} or kind in kinds:
                raise StockResearchError('规范财务事实类型无效或重复')
            kinds.add(kind)
            if not isinstance(fact['payload'], dict) or '_financial_dependencies' not in fact['payload']:
                raise StockResearchError('批次必须保留规范财务来源依赖')
            if fact['payload'].get('symbol', symbol) != symbol:
                raise StockResearchError('规范事实不能跨公司')
            origin = {'source_url': _url(fact['source_url']), 'disclosed_on': self._date(fact['disclosed_on']), 'file_sha256': _sha(fact['content_sha256'])}
            prepared.append(self._evidence(symbol, kind, fact['payload'], origin, confirmable=True))
        added = 0
        with self._write() as connection:
            revision = self._revision(connection, symbol, expected_revision)
            for item in prepared:
                self._validate_financial_dependencies(connection, item)
                added += int(self._insert_evidence(connection, item, self._now().isoformat()))
            if added:
                revision += 1
                connection.execute('UPDATE stocks_companies SET revision=? WHERE symbol=?', (revision, symbol))
        return {'evidence_ids': [item['evidence_id'] for item in prepared], 'facts_added': added, 'revision': revision,
                'requires_review': True, 'formal_screening_enabled': False, 'members_published': 0}

    def add_evidence(self, symbol: str, *, kind: str, payload: dict[str, Any], source_url: str,
                     disclosed_on: date, content_sha256: str, expected_revision: int) -> dict[str, Any]:
        self._check_revision(expected_revision)
        symbol, kind = _symbol(symbol), _text(kind, '证据种类')
        origin = {'source_url': _url(source_url), 'disclosed_on': self._date(disclosed_on),
                  'file_sha256': _sha(content_sha256)}
        if isinstance(payload, dict) and payload.get('symbol', symbol) != symbol:
            raise StockResearchError('证据不能跨公司')
        confirmable = kind not in {'research_hint', 'unresolved'}
        if kind in {'document', 'financial_document'}:
            confirmable = isinstance(payload, dict) and payload.get('status') == 'matched_template'
        row = self._evidence(symbol, kind, payload, origin, confirmable=confirmable)
        now = self._now().isoformat()
        with self._write() as connection:
            revision = self._revision(connection, symbol, expected_revision)
            self._validate_financial_dependencies(connection, row)
            added = self._insert_evidence(connection, row, now)
            if added:
                revision += 1
                connection.execute('UPDATE stocks_companies SET revision=? WHERE symbol=?', (revision, symbol))
        return {'evidence_id': row['evidence_id'], 'reused': not added, 'revision': revision}

    def save_review(self, symbol: str, evidence_id: str, *, expected_revision: int, decision: str,
                    reviewer: str, reason: str) -> dict[str, Any]:
        self._check_revision(expected_revision)
        symbol, evidence_id = _symbol(symbol), _sha(evidence_id)
        if not isinstance(decision, str) or decision not in {'confirmed', 'rejected', 'needs_evidence'}:
            raise StockResearchError('无效审核决定')
        reviewer, reason = _text(reviewer, '审核人'), _text(reason, '审核理由')
        now = self._now().isoformat()
        with self._write() as connection:
            revision = self._revision(connection, symbol, expected_revision)
            row = connection.execute('SELECT * FROM stocks_evidence WHERE evidence_id=? AND symbol=?', (evidence_id, symbol)).fetchone()
            if row is None:
                raise StockResearchError('证据不存在或属于其他公司')
            if decision == 'confirmed' and not row['confirmable']:
                raise StockResearchError('该线索缺少可确认的原文追踪或未成功提取')
            if decision == 'confirmed':
                self._validate_financial_dependencies(connection, dict(row))
            cursor = connection.execute('INSERT INTO stocks_reviews(symbol,evidence_id,decision,reviewer,reason,reviewed_at) VALUES (?,?,?,?,?,?)',
                                        (symbol, evidence_id, decision, reviewer, reason, now))
            revision += 1
            connection.execute('UPDATE stocks_companies SET revision=? WHERE symbol=?', (revision, symbol))
        return {'review_id': cursor.lastrowid, 'symbol': symbol, 'evidence_id': evidence_id, 'decision': decision,
                'reviewer': reviewer, 'reason': reason, 'reviewed_at': now, 'revision': revision}

    def get_company(self, symbol: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute('SELECT * FROM stocks_companies WHERE symbol=?', (_symbol(symbol),)).fetchone()
        return {**dict(row), 'admission_status': 'pending_verification'} if row else None

    def list_companies(self, *, search: str = '', page: int = 1, page_size: int = 15) -> dict[str, Any]:
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in (page, page_size)):
            raise StockResearchError('页码与页面大小必须为正整数')
        if not isinstance(search, str):
            raise StockResearchError('搜索条件须为字符串')
        pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        where = " WHERE symbol LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\'"
        with closing(self._connect()) as connection:
            total = connection.execute('SELECT COUNT(*) FROM stocks_companies' + where, (pattern, pattern)).fetchone()[0]
            offset = (page - 1) * page_size
            rows = (connection.execute('SELECT * FROM stocks_companies' + where + ' ORDER BY symbol LIMIT ? OFFSET ?',
                                       (pattern, pattern, min(page_size, total), offset)).fetchall()
                    if offset < total else [])
        return {'items': [{**dict(row), 'admission_status': 'pending_verification'} for row in rows],
                'total': total, 'page': page, 'page_size': page_size}

    def list_evidence(self, symbol: str) -> list[dict[str, Any]]:
        symbol = _symbol(symbol)
        now = self._now()
        with closing(self._connect()) as connection:
            rows = connection.execute('SELECT * FROM stocks_evidence WHERE symbol=? ORDER BY rowid', (symbol,)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                review = connection.execute('SELECT * FROM stocks_reviews WHERE evidence_id=? ORDER BY review_id DESC LIMIT 1',
                                             (row['evidence_id'],)).fetchone()
                status = 'pending_verification'
                if review:
                    reviewed = datetime.fromisoformat(review['reviewed_at'])
                    if reviewed <= now:
                        status = review['decision']
                        if status == 'confirmed' and now - reviewed >= timedelta(days=90):
                            status = 'expired'
                item.update(payload=json.loads(item['payload']), confirmable=bool(item['confirmable']),
                            latest_review=dict(review) if review else None, review_status=status)
                if '_financial_dependencies' in item['payload']:
                    try:
                        self._validate_financial_dependencies(connection, item)
                        item['lineage_status'] = 'current'
                    except StockResearchError as exc:
                        item.update(review_status='needs_evidence', lineage_status='stale', lineage_reason=str(exc))
                result.append(item)
        return result

    def review_history(self, symbol: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute('SELECT * FROM stocks_reviews WHERE symbol=? ORDER BY review_id', (_symbol(symbol),)).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            return {key: connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                    for key, table in (('companies', 'stocks_companies'), ('evidence', 'stocks_evidence'),
                                       ('reviews', 'stocks_reviews'), ('batches', 'stocks_source_batches'))}
