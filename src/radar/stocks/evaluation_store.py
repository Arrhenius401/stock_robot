"""不可变保存研究计算与完整来源，动态校验不代表正式成员资格。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import Any

from radar.stocks import admission, style
from radar.stocks.company_profile_source import load_company_profile_source
from radar.stocks.dividend_source import load_dividend_source
from radar.stocks.financial_source import load_financial_source
from radar.stocks.growth_source import load_growth_source
from radar.stocks.industry_source import load_industry_source
from radar.stocks.market_value_registry import load_market_value_source
from radar.stocks.research_store import (
    StockResearchError,
    StockResearchStore,
    StockRevisionConflict,
    _id,
    _json,
    _sha,
    _symbol,
)
from radar.stocks.turnover_source import load_turnover_source


def _copy(value: Any) -> Any:
    return json.loads(_json(value))


def _no_publication(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'formal_screening_enabled' and child is not False:
                raise StockResearchError('研究档案不能启用正式筛选')
            if key in {'members_published', 'eligible_members'} and (type(child) is not int or child != 0):
                raise StockResearchError('研究档案不能发布成员')
            if key in {'ready', 'eligible', 'publication_enabled'} and child is not False:
                raise StockResearchError('研究档案不能携带发布授权')
            if key.endswith('status') and isinstance(child, str) and child in {'ready', 'eligible', 'published', 'active'}:
                raise StockResearchError('研究档案不能具有可发布资格')
            _no_publication(child)
    elif isinstance(value, list):
        for child in value:
            _no_publication(child)


def _validate_references(value: Any, identifiers: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'evidence_ids' and (not isinstance(child, list) or any(not isinstance(item, str) or item not in identifiers for item in child)):
                raise StockResearchError('报告引用了公司输入之外的证据')
            _validate_references(child, identifiers)
    elif isinstance(value, list):
        for child in value:
            _validate_references(child, identifiers)


def _invalid_nodes(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get('evaluation_valid') is False or any(_invalid_nodes(child) for child in value.values())
    return isinstance(value, list) and any(_invalid_nodes(child) for child in value)


def _validate_pool(node: Any) -> None:
    if not isinstance(node, dict):
        raise StockResearchError('股票池或分支结果须为对象')
    choices = {
        'approved_conditions_status': {'passed', 'failed', 'pending_verification'},
        'style_status': {'failed', 'pending_configuration', 'pending_verification'},
        'admission_status': {'excluded', 'pending_verification'},
        'opportunity_status': {'observe', 'unavailable'},
    }
    for key, allowed in choices.items():
        if not isinstance(node.get(key), str) or node[key] not in allowed:
            raise StockResearchError('股票池或分支缺少有效的研究状态')
    if not isinstance(node.get('checks'), list) or not isinstance(node.get('unconfigured_rules'), list):
        raise StockResearchError('股票池或分支缺少检查和未配置规则')
    if any(not isinstance(rule, str) or not rule for rule in node['unconfigured_rules']):
        raise StockResearchError('未配置规则列表无效')
    if any(not isinstance(check, dict) for check in node['checks']):
        raise StockResearchError('股票池检查格式无效')
    branches = node.get('branches', {})
    if not isinstance(branches, dict):
        raise StockResearchError('风格分支须为对象')
    for branch in branches.values():
        _validate_pool(branch)


class StockEvaluationStore:
    """只保存研究档案；同库独立版本表，不改变研究或ETF存储版本。"""

    def __init__(self, research: StockResearchStore):
        self.research = research
        with research._write() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_evaluation_schema_versions (version INTEGER NOT NULL)')
            versions = connection.execute('SELECT version FROM stocks_evaluation_schema_versions').fetchall()
            if versions and (len(versions) != 1 or versions[0][0] != 1):
                raise StockResearchError('不支持的研究评估档案版本')
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_evaluations (evaluation_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, as_of TEXT NOT NULL, scope TEXT NOT NULL, report TEXT NOT NULL, inputs TEXT NOT NULL, context TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_evaluation_companies (evaluation_id TEXT NOT NULL REFERENCES stocks_evaluations(evaluation_id), symbol TEXT NOT NULL, source_revision INTEGER NOT NULL, PRIMARY KEY(evaluation_id,symbol))')
            if not versions:
                connection.execute('INSERT INTO stocks_evaluation_schema_versions VALUES (1)')

    def _roster(self) -> list[dict[str, Any]]:
        return self.research.list_companies(page_size=2**63 - 1)['items']

    def capture_inputs(self, *, symbol: str | None = None) -> dict[str, Any]:
        if symbol is None:
            listed = self._roster()
        else:
            company = self.research.get_company(_symbol(symbol))
            if company is None:
                raise StockResearchError('公司不存在')
            listed = [company]
        companies = []
        for row in listed:
            before = self.research.get_company(row['symbol'])
            evidence = self.research.list_evidence(row['symbol'])
            after = self.research.get_company(row['symbol'])
            if before is None or after is None or before['revision'] != after['revision'] or before['revision'] != row['revision']:
                raise StockRevisionConflict('截取研究输入时公司资料已变化')
            companies.append({'company': before, 'evidence': evidence})
        inputs = {'scope': 'all' if symbol is None else 'single', 'companies': companies,
                  'captured_at': self.research._now().isoformat()}
        with closing(self.research._connect()) as connection:
            self._assert_sources(inputs, connection)
        return _copy(inputs)

    def _manifest(self, inputs: Any) -> dict[str, Any]:
        inputs = _copy(inputs)
        if (not isinstance(inputs, dict) or not isinstance(inputs.get('scope'), str)
                or inputs['scope'] not in {'all', 'single'} or not isinstance(inputs.get('companies'), list)):
            raise StockResearchError('研究输入缺少有效范围与公司列表')
        captured = inputs.get('captured_at')
        try:
            captured_time = datetime.fromisoformat(captured) if isinstance(captured, str) else None
            if captured_time is None or captured_time.tzinfo is None or captured_time.utcoffset() is None or captured_time > self.research._now():
                raise StockResearchError('输入捕获时间无效或位于未来')
        except ValueError as exc:
            raise StockResearchError('输入捕获时间无效') from exc
        seen: set[str] = set()
        for entry in inputs['companies']:
            if not isinstance(entry, dict) or not isinstance(entry.get('company'), dict) or not isinstance(entry.get('evidence'), list):
                raise StockResearchError('每家公司须保留完整资料与证据')
            company = entry['company']
            symbol = _symbol(company.get('symbol'))
            if symbol in seen:
                raise StockResearchError('输入公司代码重复')
            seen.add(symbol)
            revision = company.get('revision')
            if type(revision) is not int or not 0 <= revision <= 2**63 - 1:
                raise StockResearchError('公司来源revision无效')
            self.research._date(company.get('data_as_of'))
            if 'turnover_source' in entry or 'turnover_as_of' in entry:
                turnover = entry.get('turnover_source')
                turnover_as_of = self.research._date(entry.get('turnover_as_of'))
                if not isinstance(turnover, dict) or turnover.get('symbol') != symbol or turnover.get('evaluation_as_of') != turnover_as_of:
                    raise StockResearchError('公司成交额来源的身份或评估截点无效')
            if 'dividend_source' in entry or 'dividend_as_of' in entry:
                dividend = entry.get('dividend_source')
                dividend_as_of = self.research._date(entry.get('dividend_as_of'))
                if not isinstance(dividend, dict) or dividend.get('symbol') != symbol or dividend.get('evaluation_as_of') != dividend_as_of:
                    raise StockResearchError('公司分红来源的身份或评估截点无效')
            if 'financial_source' in entry or 'financial_as_of' in entry:
                financial = entry.get('financial_source')
                financial_as_of = self.research._date(entry.get('financial_as_of'))
                if (not isinstance(financial, dict) or financial.get('symbol') != symbol
                        or financial.get('evaluation_as_of') != financial_as_of):
                    raise StockResearchError('公司财务来源的身份或评估截点无效')
            if 'market_value_source' in entry or 'market_value_as_of' in entry:
                market = entry.get('market_value_source')
                market_as_of = self.research._date(entry.get('market_value_as_of'))
                if (not isinstance(market, dict) or market.get('symbol') != symbol
                        or market.get('evaluation_as_of') != market_as_of):
                    raise StockResearchError('公司市值来源的身份或评估截点无效')
            for key, day_key in (('company_profile_source', 'profile_as_of'), ('industry_source', 'industry_as_of'), ('growth_source', 'growth_as_of')):
                if key in entry or day_key in entry:
                    source = entry.get(key)
                    cutoff = self.research._date(entry.get(day_key))
                    if not isinstance(source, dict) or source.get('symbol') != symbol or source.get('evaluation_as_of') != cutoff:
                        raise StockResearchError('上市或行业来源身份/截点无效')
            evidence_ids: set[str] = set()
            for row in entry['evidence']:
                if not isinstance(row, dict) or row.get('symbol') != symbol or not isinstance(row.get('payload'), dict):
                    raise StockResearchError('证据缺少原始内容或跨公司归属')
                identifier = _sha(row.get('evidence_id'))
                if identifier in evidence_ids:
                    raise StockResearchError('公司证据重复')
                evidence_ids.add(identifier)
                review = row.get('latest_review')
                if review is not None and (not isinstance(review, dict) or review.get('symbol') != symbol or review.get('evidence_id') != identifier):
                    raise StockResearchError('证据最新审核归属不一致')
            entry['evidence'].sort(key=lambda row: row['evidence_id'])
        if inputs['scope'] == 'single' and len(inputs['companies']) != 1:
            raise StockResearchError('单公司范围必须恰有一家公司')
        inputs['companies'].sort(key=lambda entry: entry['company']['symbol'])
        return inputs

    def _assert_sources(self, inputs: dict[str, Any], connection: sqlite3.Connection, *, full: bool = False) -> None:
        current = {row['symbol']: dict(row) for row in connection.execute('SELECT * FROM stocks_companies')}
        supplied = {entry['company']['symbol']: entry for entry in inputs['companies']}
        if inputs['scope'] == 'all' and set(current) != set(supplied):
            raise StockRevisionConflict('全部候选名录已变化')
        for symbol, entry in supplied.items():
            expected = entry['company']
            live = current.get(symbol)
            if live is None or live['revision'] != expected['revision']:
                raise StockRevisionConflict(f'公司来源版本已变化：{symbol}')
            if 'turnover_source' in entry:
                cutoff = date.fromisoformat(entry['turnover_as_of'])
                if _json(load_turnover_source(self.research.db_path, symbol, cutoff)) != _json(entry['turnover_source']):
                    raise StockRevisionConflict(f'公司成交额来源已变化：{symbol}')
            if 'dividend_source' in entry:
                cutoff = date.fromisoformat(entry['dividend_as_of'])
                if _json(load_dividend_source(self.research.db_path, symbol, cutoff)) != _json(entry['dividend_source']):
                    raise StockRevisionConflict(f'公司分红来源已变化：{symbol}')
            if 'financial_source' in entry:
                cutoff = date.fromisoformat(entry['financial_as_of'])
                live_financial = load_financial_source(self.research.db_path, symbol, cutoff)
                if _json(live_financial) != _json(entry['financial_source']):
                    raise StockRevisionConflict(f'公司财务来源已变化：{symbol}')
            if 'market_value_source' in entry:
                cutoff = date.fromisoformat(entry['market_value_as_of'])
                live_market = load_market_value_source(self.research.db_path.parent, symbol, cutoff, db=self.research.db_path)
                if _json(live_market) != _json(entry['market_value_source']):
                    raise StockRevisionConflict(f'公司市值来源已变化：{symbol}')
            for key, day_key, loader in (
                    ('company_profile_source', 'profile_as_of', load_company_profile_source),
                    ('industry_source', 'industry_as_of', load_industry_source),
                    ('growth_source', 'growth_as_of', load_growth_source)):
                if key in entry:
                    if key == 'growth_source':
                        loaded = load_growth_source(self.research.db_path.parent, symbol, date.fromisoformat(entry[day_key]), db=self.research.db_path)
                    else:
                        loaded = loader(self.research.db_path.parent, symbol, date.fromisoformat(entry[day_key]), db=self.research.db_path)
                    if _json(loaded) != _json(entry[key]):
                        raise StockRevisionConflict(f'上市或行业来源已变化：{symbol}')
            if full:
                live['admission_status'] = 'pending_verification'
                if _json(live) != _json(expected):
                    raise StockRevisionConflict(f'公司原始资料不匹配：{symbol}')
                evidence = self.research.list_evidence(symbol)
                evidence.sort(key=lambda row: row['evidence_id'])
                if _json(evidence) != _json(entry['evidence']):
                    raise StockRevisionConflict(f'证据原始资料或审核已变化：{symbol}')

    def _report(self, report: Any, inputs: dict[str, Any]) -> tuple[dict[str, Any], str]:
        report = _copy(report)
        if not isinstance(report, dict):
            raise StockResearchError('评估报告必须为对象')
        _no_publication(report)
        if (report.get('formal_screening_enabled') is not False or type(report.get('members_published')) is not int
                or report['members_published'] != 0 or report.get('research_only') is not True):
            raise StockResearchError('报告须明确为关闭正式发布的研究结果')
        as_of = self.research._date(report.get('as_of'))
        if report.get('rule_version') != admission.RULE_VERSION or report.get('style_rule_version') != style.STYLE_RULE_VERSION:
            raise StockResearchError('共同或风格规则版本与当前计算器不符')
        items, errors = report.get('items'), report.get('errors')
        if not isinstance(items, list) or not isinstance(errors, list):
            raise StockResearchError('报告缺少计算结果或异常列表')
        expected = {entry['company']['symbol']: entry['company']['revision'] for entry in inputs['companies']}
        seen: set[str] = set()
        partial = bool(errors) or _invalid_nodes(report)
        for item in items:
            if not isinstance(item, dict):
                raise StockResearchError('公司评估须为对象')
            symbol = _symbol(item.get('symbol'))
            if symbol in seen or symbol not in expected:
                raise StockResearchError('公司结果重复或不属于输入来源')
            seen.add(symbol)
            if item.get('as_of') != as_of or item.get('rule_version') != admission.RULE_VERSION or item.get('style_rule_version') != style.STYLE_RULE_VERSION:
                raise StockResearchError('公司截点或规则版本不匹配')
            source = next(entry for entry in inputs['companies'] if entry['company']['symbol'] == symbol)
            pool_results = item.get('pools')
            growth_pool = pool_results.get('growth_trend') if isinstance(pool_results, dict) else None
            if not isinstance(growth_pool, dict):
                raise StockResearchError('成长池结果格式无效')
            nested_growth = growth_pool.get('growth_research')
            if _json(nested_growth) != _json(item.get('growth_source')):
                raise StockResearchError('成长研究展示结果与绑定来源不匹配')
            for key, day_key in (('company_profile_source', 'profile_as_of'), ('industry_source', 'industry_as_of'), ('growth_source', 'growth_as_of')):
                if (key in item or key in source) and (source.get(day_key) != as_of or _json(source.get(key)) != _json(item.get(key))):
                    raise StockResearchError('报告上市或行业来源与归档输入不匹配')
            if ('market_value_source' in item or 'market_value_source' in source) and (
                    source.get('market_value_as_of') != as_of or _json(source.get('market_value_source')) != _json(item.get('market_value_source'))):
                raise StockResearchError('报告市值来源与归档输入不匹配')
            if ('dividend_source' in source or 'dividend_source' in item) and (
                    source.get('dividend_as_of') != as_of or _json(source.get('dividend_source')) != _json(item.get('dividend_source'))):
                raise StockResearchError('报告分红来源与归档输入不匹配')
            if ('turnover_source' in source or 'turnover_source' in item) and (
                    source.get('turnover_as_of') != as_of or _json(source.get('turnover_source')) != _json(item.get('turnover_source'))):
                raise StockResearchError('报告成交额来源与归档输入不匹配')
            if ('financial_source' in source or 'financial_source' in item) and (
                    source.get('financial_as_of') != as_of or _json(source.get('financial_source')) != _json(item.get('financial_source'))):
                raise StockResearchError('报告财务来源与归档输入不匹配')
            if item.get('formal_screening_enabled') is not False:
                raise StockResearchError('公司结果须关闭正式筛选')
            if not isinstance(item.get('pools'), dict) or set(item['pools']) != {'dividend_value', 'growth_trend'}:
                raise StockResearchError('公司结果缺少两个股票池研究结果')
            if type(item.get('evaluation_valid')) is not bool:
                raise StockResearchError('公司结果必须明确计算有效性')
            if not isinstance(item.get('checks'), list) or item.get('common_status') not in ('passed', 'failed', 'pending_verification'):
                raise StockResearchError('共同条件结果结构无效')
            for pool in item['pools'].values():
                _validate_pool(pool)
            branches = item['pools']['dividend_value'].get('branches')
            if not isinstance(branches, dict) or set(branches) != {'dividend', 'value'}:
                raise StockResearchError('红利价值结果缺少两条独立分支')
            source_ids = {row['evidence_id'] for entry in inputs['companies'] if entry['company']['symbol'] == symbol for row in entry['evidence']}
            _validate_references(item, source_ids)
            revision = item.get('source_revision')
            failed = item.get('evaluation_valid') is False
            if revision is None and failed:
                partial = True
            elif type(revision) is not int or revision != expected[symbol]:
                raise StockRevisionConflict(f'报告来源版本与输入不匹配：{symbol}')
            partial = partial or failed
        if seen != set(expected):
            raise StockResearchError('公司结果没有完整覆盖输入范围')
        report['items'].sort(key=lambda item: item['symbol'])
        return report, 'partial' if partial else 'complete'

    def save_evaluation(self, report: dict[str, Any], inputs: dict[str, Any], *,
                        market_window: list[Any] | None = None) -> dict[str, Any]:
        manifest = self._manifest(inputs)
        prepared, status = self._report(report, manifest)
        window = None
        if market_window is not None:
            if not isinstance(market_window, list):
                raise StockResearchError('市场窗口须为60个研究日期')
            window = [self.research._date(day) for day in market_window]
            if len(window) != 60 or len(set(window)) != 60 or window != sorted(window) or window[-1] > prepared['as_of']:
                raise StockResearchError('市场窗口须为递增唯一且不晚于研究截点的60日')
        trace = prepared.get('market_window_source')
        calendar = trace.get('calendar') if isinstance(trace, dict) else None
        for entry in manifest['companies']:
            source = entry.get('market_value_source')
            if (isinstance(source, dict) and source.get('status') == 'current'
                    and (window is None or source.get('window_days') != window or not isinstance(calendar, dict)
                         or source.get('calendar_sha256') != calendar.get('snapshot_sha256'))):
                raise StockResearchError('市值来源与评估日历不一致')
            turnover = entry.get('turnover_source')
            if (isinstance(turnover, dict) and turnover.get('status') == 'current'
                    and (window is None or turnover.get('window_days') != window or not isinstance(calendar, dict)
                         or turnover.get('calendar_sha256') != calendar.get('snapshot_sha256'))):
                raise StockResearchError('成交额来源与评估日历不一致')
        context = {'market_window': window, 'market_window_role': 'research_context_only',
                   'market_window_source': prepared.get('market_window_source')}
        identity_inputs = {key: value for key, value in manifest.items() if key != 'captured_at'}
        identifier = _id({'report': prepared, 'inputs': identity_inputs, 'context': context})
        now = self.research._now().isoformat()
        with self.research._write() as connection:
            self._assert_sources(manifest, connection, full=True)
            connection.execute('INSERT OR IGNORE INTO stocks_evaluations VALUES (?,?,?,?,?,?,?,?)',
                               (identifier, status, now, prepared['as_of'], manifest['scope'], _json(prepared), _json(manifest), _json(context)))
            for entry in manifest['companies']:
                company = entry['company']
                connection.execute('INSERT OR IGNORE INTO stocks_evaluation_companies VALUES (?,?,?)',
                                   (identifier, company['symbol'], company['revision']))
        saved = self.get(identifier)
        if saved is None:
            raise StockResearchError('评估档案保存后不可读')
        return saved

    def _freshness(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        reasons: list[str] = []
        if snapshot['status'] != 'complete':
            reasons.append('partial_evaluation')
        now = self.research._now()
        if snapshot['as_of'] != now.astimezone(admission._BJ).date().isoformat():
            reasons.append('historical_as_of')
        report = snapshot['report']
        if report.get('rule_version') != admission.RULE_VERSION:
            reasons.append('rule_version_changed')
        if report.get('style_rule_version') != style.STYLE_RULE_VERSION:
            reasons.append('style_rule_version_changed')
        try:
            with closing(self.research._connect()) as connection:
                self._assert_sources(snapshot['inputs'], connection)
        except StockRevisionConflict as exc:
            reasons.append('source_revision_changed:' + str(exc))
        for entry in snapshot['inputs']['companies']:
            for row in entry['evidence']:
                review = row.get('latest_review')
                if not isinstance(review, dict) or review.get('decision') != 'confirmed':
                    continue
                reviewed_at = review.get('reviewed_at')
                try:
                    reviewed = datetime.fromisoformat(reviewed_at) if isinstance(reviewed_at, str) else None
                    if reviewed is None or reviewed.tzinfo is None or reviewed.utcoffset() is None or now - reviewed >= timedelta(days=90):
                        reasons.append('review_expired:' + row['evidence_id'])
                except (ValueError, OverflowError):
                    reasons.append('invalid_review_time:' + row['evidence_id'])
        return {'current': not reasons, 'reasons': sorted(set(reasons))}

    def get(self, evaluation_id: str) -> dict[str, Any] | None:
        identifier = _sha(evaluation_id)
        with closing(self.research._connect()) as connection:
            row = connection.execute('SELECT * FROM stocks_evaluations WHERE evaluation_id=?', (identifier,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ('report', 'inputs', 'context'):
            result[key] = json.loads(result[key])
        result['freshness'] = self._freshness(result)
        return result

    def assert_current(self, evaluation_id: str) -> dict[str, Any]:
        snapshot = self.get(evaluation_id)
        if snapshot is None:
            raise StockResearchError('评估档案不存在')
        if not snapshot['freshness']['current']:
            reasons = snapshot['freshness']['reasons']
            if any(reason.startswith('source_revision_changed:') for reason in reasons):
                raise StockRevisionConflict('评估来源已变化，请重新计算')
            raise StockResearchError('评估档案不能作为当前依据：' + ','.join(reasons))
        return snapshot

    def list_evaluations(self, *, symbol: str | None = None, page: int = 1, page_size: int = 15) -> dict[str, Any]:
        if any(type(value) is not int or value < 1 for value in (page, page_size)):
            raise StockResearchError('页码与页大小必须为正整数')
        parameters: tuple[Any, ...] = ()
        where = ''
        if symbol is not None:
            where = ' WHERE EXISTS(SELECT 1 FROM stocks_evaluation_companies c WHERE c.evaluation_id=e.evaluation_id AND c.symbol=?)'
            parameters = (_symbol(symbol),)
        with closing(self.research._connect()) as connection:
            total = connection.execute('SELECT COUNT(*) FROM stocks_evaluations e' + where, parameters).fetchone()[0]
            offset = (page - 1) * page_size
            rows = (connection.execute('SELECT evaluation_id,status,created_at,as_of,scope FROM stocks_evaluations e' + where
                                       + ' ORDER BY created_at DESC,evaluation_id LIMIT ? OFFSET ?',
                                       (*parameters, min(page_size, total), offset)).fetchall() if offset < total else [])
        return {'items': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size}
