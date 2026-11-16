"""财务任务产物和网页摘要；数值初筛不授予入池资格。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from radar.stocks.research_store import StockResearchError, _sha
from radar.stocks.structured_screen import screen_structured_financials

NOTICE = '本任务复核8项盈利数值；审计意见另按同源原文核验，身份、治理、行业地位和金融适用性仍须独立判断，不调整正式成员。'


def financial_progress(task: dict[str, Any]) -> dict[str, int]:
    latest = {item['scope']: item for item in task['results'] if item['scope'].startswith('financial:')}
    total = len(task['input_snapshot'].get('companies', []))
    counts = {'total': total, 'attempted': len(latest), 'not_attempted': total - len(latest),
              'numeric_passed': 0, 'numeric_failed': 0, 'pending_verification': 0}
    for item in latest.values():
        outcome = item.get('artifact', {}).get('financial_numeric_status', 'pending_verification')
        counts[{'passed': 'numeric_passed', 'failed': 'numeric_failed'}.get(outcome, 'pending_verification')] += 1
    return counts


def verify_financial_payload(payload: Any, task: dict[str, Any], symbol: str) -> dict[str, Any]:
    if (not isinstance(payload, dict) or payload.get('symbol') != symbol
            or payload.get('as_of') != task['target_date']):
        raise StockResearchError('财务产物公司或研究日期不匹配')
    collection = payload.get('collection')
    if collection is None:
        return {'symbol': symbol, 'financial_numeric_status': 'pending_verification',
                'financial_numeric_checks': [], 'cash_flow_reference': {}, 'issuer_applicability': 'unverified',
                'source_diagnostics': {'code': 'source_error', 'message': '来源请求或数据处理失败，资料保留待补；详细信息见服务日志。'}}
    if (not isinstance(collection, dict) or collection.get('symbol') != symbol
            or collection.get('as_of') != task['target_date']):
        raise StockResearchError('财务原始结构化产物身份不匹配')
    result = screen_structured_financials(collection)
    # 只映射受控诊断，第三方异常原文可能包含本地路径，留在服务日志。
    stopped = collection.get('source_stopped') is True
    errors = bool(collection.get('errors'))
    result['source_diagnostics'] = {
        'code': 'source_stopped' if stopped else ('source_error' if errors else 'complete'),
        'message': ('来源限流（HTTP 429），已停止本次后续采集；已保存的进度保留。' if stopped else
                    '结构化数据来源异常，未取得的资料保留待补；可稍后继续复核。' if errors else '结构化来源已读取。'),
    }
    return result


def safe_financial_result(result: dict[str, Any], name: str) -> dict[str, Any]:
    # 来源只公开字段口径和时间，不暴露工作目录或完整下载响应。
    def safe(value: Any) -> Any:
        if isinstance(value, list):
            return [safe(entry) for entry in value]
        if isinstance(value, dict):
            return {key: safe(child) for key, child in value.items()
                    if key not in {'path', 'cache_path', 'raw_path', 'raw_artifact', 'records', 'collection'}}
        return value
    fields = ('symbol', 'as_of', 'rule_version', 'financial_numeric_status', 'financial_numeric_checks',
              'cash_flow_reference', 'issuer_applicability', 'source_company_type', 'uncovered_conditions', 'source_diagnostics')
    return {**safe({key: result[key] for key in fields if key in result}), 'name': name,
            'financial_verified': False, 'formal_screening_enabled': False, 'members_published': 0}


def read_financial_artifact(item: dict[str, Any], artifact_root: Path) -> Any:
    reference = item.get('artifact', {})
    sha, size = _sha(reference.get('sha256')), reference.get('bytes')
    path = artifact_root / (sha + '.json')
    if (type(size) is not int or not 0 < size <= 64 * 1024 * 1024
            or any(parent.is_symlink() for parent in (path, *path.parents))
            or Path(str(reference.get('path', ''))).resolve() != path.resolve()
            or not path.is_file() or path.stat().st_size != size):
        raise StockResearchError('财务产物缺失、路径或长度异常')
    with path.open('rb') as stream:
        raw = stream.read(size + 1)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
        raise StockResearchError('财务产物摘要不匹配')
    return json.loads(raw)


def financial_results(task: dict[str, Any], artifact_root: Path, page: int) -> dict[str, Any]:
    if task['kind'] != 'financial_review':
        raise StockResearchError('该任务没有财务复核结果')
    companies = task['input_snapshot']['companies']
    latest = {item['scope']: item for item in task['results']}
    items = []
    for company in companies[(page - 1) * 15:page * 15]:
        symbol = company['symbol']
        item = latest.get('financial:' + symbol)
        if item is None:
            items.append({'symbol': symbol, 'name': company['name'], 'financial_numeric_status': 'not_attempted',
                          'financial_numeric_checks': [], 'cash_flow_reference': {}, 'issuer_applicability': 'unverified'})
            continue
        payload = read_financial_artifact(item, artifact_root)
        items.append(safe_financial_result(verify_financial_payload(payload, task, symbol), company['name']))
    return {'items': items, 'as_of': task['target_date'], 'summary': financial_progress(task), 'page': page, 'page_size': 15,
            'total': len(companies), 'notice': NOTICE, 'formal_screening_enabled': False, 'members_published': 0}
