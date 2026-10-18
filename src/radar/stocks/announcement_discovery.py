"""巨潮公告发现：只确认查询范围，不确认财务金额或年度完整性。"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)
_LOOKUP = 'https://www.cninfo.com.cn/new/information/topSearch/query'
_QUERY = 'https://www.cninfo.com.cn/new/hisAnnouncement/query'
_TERMS = ('权益分派', '利润分配')


def _save(path: Path, value: Any) -> None:
    descriptor, name = tempfile.mkstemp(prefix='.discovery-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _identity(payload: Any, symbol: str) -> str:
    if not isinstance(payload, list):
        raise TypeError('公司查询结构不是列表')
    matches = [item for item in payload if isinstance(item, dict)
               and item.get('code') == symbol and item.get('category') == 'A股']
    if not matches or any(not isinstance(item.get('orgId'), str) or not item['orgId'].strip()
                          for item in matches):
        raise ValueError('缺少精确 A 股公司身份')
    identifiers = {item['orgId'] for item in matches}
    if len(identifiers) != 1:
        raise ValueError('公司身份存在冲突')
    return identifiers.pop()


def _document(item: Any, symbol: str, org_id: str, start: date, end: date, *, mode: str = "dividend") -> dict[str, Any] | None:
    if not isinstance(item, dict) or item.get('secCode') != symbol or item.get('orgId') != org_id:
        raise ValueError('公告公司身份不一致')
    identifier = item.get('announcementId')
    title = item.get('announcementTitle')
    timestamp = item.get('announcementTime')
    path = item.get('adjunctUrl')
    if not isinstance(identifier, str) or not identifier or not isinstance(title, str):
        raise ValueError('公告 ID 或标题缺失')
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
        raise TypeError('公告时间缺失')
    try:
        published = datetime.fromtimestamp(timestamp / 1000, UTC).astimezone(ZoneInfo('Asia/Shanghai')).date()
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError('公告时间无效') from exc
    if not start <= published <= end:
        raise ValueError('公告日期不在查询范围')
    if not isinstance(path, str) or not re.fullmatch(r'finalpage/\d{4}-\d{2}-\d{2}/[A-Za-z0-9_-]+\.[Pp][Dd][Ff]', path):
        raise ValueError('公告 URL 不是安全的官方 PDF 相对路径')
    if unquote(path) != path:
        raise ValueError('公告 URL 不能包含编码路径')
    clean_title = re.sub(r'<[^>]*>', '', html.unescape(title)).strip()
    if mode == "interim_report":
        normalized = re.sub(r"\s+", "", clean_title)
        report = re.search(r"(20\d{2})年?(第一季度|半年度|第三季度)报告", normalized)
        if not report or any(word in normalized for word in ("摘要", "英文", "审计报告")):
            return None
        review = any(word in normalized for word in ("更正", "取消", "撤销", "修订", "更新", "补充", "变更", "延期", "终止"))
        if not review and normalized[report.end():] not in {"", "（全文）", "(全文)"}:
            return None
        months = {"第一季度": 3, "半年度": 6, "第三季度": 9}[report[2]]
        year = int(report[1])
        period = date(year, months, 31 if months == 3 else 30)
        if period >= published:
            raise ValueError("季度报告期间尚未结束或披露日期无效")
        return {"symbol": symbol, "org_id": org_id, "announcement_id": identifier,
                "title": clean_title, "disclosed_on": published.isoformat(), "fiscal_year": year,
                "period_months": months, "period_end": period.isoformat(),
                "source_url": "https://static.cninfo.com.cn/" + path,
                "status": "pending_review" if review else "discovered", "kind": "interim_report",
                "discovery_status": "pending_review" if review else "discovered", "financial_verified": False}
    if mode == "annual_report":
        report = re.search(r"(20\d{2})年?年度报告", clean_title)
        if not report or any(word in clean_title for word in ("英文", "审计报告")):
            return None
        review = any(word in clean_title for word in ("更正", "取消", "撤销", "修订", "更新", "补充", "变更", "延期", "终止"))
        if not review and ("摘要" in clean_title or clean_title[report.end():].strip() not in {"", "（全文）", "(全文)"}):
            return None
        return {"symbol": symbol, "org_id": org_id, "announcement_id": identifier,
                "title": clean_title, "disclosed_on": published.isoformat(), "fiscal_year": int(report[1]),
                "source_url": "https://static.cninfo.com.cn/" + path,
                "status": "pending_review" if review else "discovered", "kind": "annual_report",
                "discovery_status": "pending_review" if review else "discovered", "financial_verified": False}
    # 相关取消或调整公告保留风险提醒，不视为实施证据。
    related = any(term in clean_title for term in _TERMS)
    review = any(word in clean_title for word in ('更正', '取消', '撤销', '终止', '调整'))
    if not related or ('实施' not in clean_title and not review):
        return None
    return {'symbol': symbol, 'org_id': org_id, 'announcement_id': identifier,
            'title': clean_title, 'disclosed_on': published.isoformat(),
            'source_url': 'https://static.cninfo.com.cn/' + path,
            'status': 'pending_review' if review else 'discovered',
            'kind': 'related_review' if review else 'implementation',
            'discovery_status': 'pending_review' if review else 'discovered',
            'financial_verified': False}


def _page(payload: Any, symbol: str, org_id: str, start: date, end: date, *, mode: str = "dividend") -> None:
    if not isinstance(payload, dict):
        raise TypeError('公告查询结构不是对象')
    total = payload.get('totalAnnouncement')
    if type(total) is not int or total < 0 or total > 30000:
        raise ValueError('公告总数缺失或超出预算')
    if type(payload.get('totalRecordNum')) is not int or payload['totalRecordNum'] != total:
        raise ValueError('公告计数口径不一致')
    if 'announcements' not in payload:
        raise ValueError('公告列表字段缺失')
    rows = payload['announcements']
    # 官方真实零结果使用 null；仅显式零总数且无后续页时允许。
    if rows is None and total == 0 and payload.get('hasMore') is False:
        rows = []
    if not isinstance(rows, list) or type(payload.get('hasMore')) is not bool:
        raise ValueError('公告列表或分页状态缺失')
    if len(rows) > 30 or (not rows and (total != 0 or payload['hasMore'])):
        raise ValueError('空页或页大小与总数不一致')
    for item in rows:
        _document(item, symbol, org_id, start, end, mode=mode)


def discover_announcements(companies: list[dict[str, Any]], as_of: date, cache_dir: Path, *,
                           refresh: bool = False, http_post: Callable[..., Any] | None = None,
                           sleep: Callable[[float], Any] = time.sleep, mode: str = "dividend") -> dict[str, Any]:
    """逐公司发现实施公告，保留断点；任何查询失败均不宣称公司查询完成。"""
    if not isinstance(mode, str) or mode not in {"dividend", "annual_report", "interim_report"}:
        raise ValueError("未知公告发现模式")
    terms = (("第一季度报告", "半年度报告", "第三季度报告") if mode == "interim_report"
             else (("年度报告",) if mode == "annual_report" else _TERMS))
    if not isinstance(companies, list) or any(
        not isinstance(company, dict) or not isinstance(company.get('symbol'), str)
        or not re.fullmatch(r'(?:6\d{5}|[03]\d{5})', company['symbol']) for company in companies
    ):
        raise ValueError('companies 必须含精确六位沪深 A 股 symbol')
    if type(as_of) is not date:
        raise ValueError('as_of 必须是日期')
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError('缓存目录不能是符号链接')
    start = date(as_of.year - 3, 1, 1)
    post = http_post or requests.post
    stopped = False
    requested = False
    last_capture: dict[str, Any] = {}
    diagnostics: list[dict[str, Any]] = []

    def fetch(url: str, body: dict[str, Any], validate: Callable[[Any], Any]) -> Any:
        nonlocal stopped, requested, last_capture
        key = hashlib.sha256(json.dumps([url, body, as_of.isoformat()], sort_keys=True).encode()).hexdigest()
        path = root / (key + '.json')
        if path.is_symlink():
            raise ValueError('缓存文件不能是符号链接')
        if not refresh and path.exists():
            try:
                cached = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(cached, dict) or cached.get('endpoint') != url or cached.get('body') != body:
                    raise ValueError('缓存请求身份不一致')
                captured_at = cached.get('captured_at')
                if not isinstance(captured_at, str) or datetime.fromisoformat(captured_at).utcoffset() is None:
                    raise ValueError('缓存捕获时间缺失')
                payload = cached['raw_json']
                validate(payload)
                last_capture = {'file': path.name, 'captured_at': captured_at, 'cache_reused': True}
                return payload
            except (ValueError, TypeError, OSError, KeyError):
                logger.warning('公告缓存无效，重新请求: %s', path, exc_info=True)
        if stopped:
            raise ValueError('来源已因 429 停止')
        for attempt in range(3):
            if requested:
                sleep((3.0, 10.0)[attempt - 1] if attempt else 1.0)
            requested = True
            response = None
            try:
                response = post(url, data=body, timeout=(15, 15), allow_redirects=False,
                                headers={'User-Agent': 'StockRobot/1.0', 'Referer': 'https://www.cninfo.com.cn/'})
                status = response.status_code
                if status == 429:
                    stopped = True
                    raise ValueError('来源返回 429，本轮停止')
                if 500 <= status <= 599:
                    diagnostics.append({'endpoint': url, 'body': body, 'attempt': attempt + 1,
                                        'error': f'HTTP {status}'})
                    if attempt < 2:
                        continue
                    raise ValueError(f'来源 HTTP {status}')
                if status != 200:
                    raise ValueError(f'来源 HTTP {status}，不跟随重定向')
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ValueError('来源不是 JSON，可能为 HTML 验证页面') from exc
                validate(payload)
                captured_at = datetime.now(UTC).isoformat()
                _save(path, {'endpoint': url, 'body': body, 'captured_at': captured_at, 'raw_json': payload})
                last_capture = {'file': path.name, 'captured_at': captured_at, 'cache_reused': False}
                return payload
            except requests.RequestException as exc:
                diagnostics.append({'endpoint': url, 'body': body, 'attempt': attempt + 1, 'error': str(exc)})
                logger.warning('公告请求失败，尝试 %s', attempt + 1, exc_info=True)
                if attempt == 2:
                    raise
            except (ValueError, TypeError, OSError) as exc:
                diagnostics.append({'endpoint': url, 'body': body, 'attempt': attempt + 1, 'error': str(exc)})
                raise
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception as exc:  # noqa: BLE001 第三方响应关闭边界
                        logger.warning('公告响应关闭失败: %s', exc)
        raise ValueError('公告请求未完成')

    records = []
    all_documents: dict[tuple[str, str], dict[str, Any]] = {}
    for company in companies:
        symbol = company['symbol']
        record: dict[str, Any] = {'symbol': symbol, 'name': company.get('name', ''),
                                  'status': 'failed', 'query_states': [
                                      {'term': term, 'start': start.isoformat(), 'end': as_of.isoformat(),
                                       'status': 'not_attempted'} for term in terms],
                                  'annual_coverage_verified': False}
        company_documents: dict[str, dict[str, Any]] = {}
        try:
            lookup = fetch(_LOOKUP, {'keyWord': symbol, 'maxNum': 10}, lambda data, code=symbol: _identity(data, code))
            org_id = _identity(lookup, symbol)
            record['org_id'] = org_id
            record['identity_capture'] = dict(last_capture)
            for state in record['query_states']:
                term = state['term']
                state['status'] = 'incomplete'
                state['raw_pages'] = []
                seen: set[str] = set()
                expected = None
                for page_num in range(1, 1002):
                    body = {'stock': f'{symbol},{org_id}', 'tabName': 'fulltext',
                            'column': 'sse' if symbol.startswith('6') else 'szse',
                            'pageSize': 30, 'pageNum': page_num, 'searchkey': term,
                            'seDate': f'{start.isoformat()}~{as_of.isoformat()}', 'isHLtitle': 'true'}
                    payload = fetch(_QUERY, body, lambda data, code=symbol, org=org_id: _page(data, code, org, start, as_of, mode=mode))
                    state['raw_pages'].append(dict(last_capture))
                    total = payload['totalAnnouncement']
                    if expected is not None and expected != total:
                        raise ValueError('分页期间公告总数改变')
                    expected = total
                    for item in payload['announcements'] or []:
                        identifier = item['announcementId']
                        if identifier in seen:
                            raise ValueError('分页公告 ID 重复')
                        seen.add(identifier)
                        document = _document(item, symbol, org_id, start, as_of, mode=mode)
                        if document is not None:
                            old = company_documents.get(identifier)
                            if old is not None and old != document:
                                old['status'] = 'pending_review'
                                old['discovery_status'] = 'pending_review'
                                old['conflicting_versions'] = [dict(old), document]
                                raise ValueError('不同查询的相同公告 ID 内容冲突')
                            company_documents[identifier] = document
                    if len(seen) > total or (payload['hasMore'] and len(seen) >= total):
                        raise ValueError('分页状态与公告总数不一致')
                    if not payload['hasMore']:
                        if len(seen) != total:
                            raise ValueError('已取得公告数不等于原始总数')
                        state.update(status='query_scope_complete', raw_count=total, pages=page_num)
                        break
                else:
                    raise ValueError('分页超过预算')
            record['status'] = 'query_scope_complete'
        # 第三方 HTTP、响应与磁盘操作作为逐公司隔离边界，记录失败而不阻断其它公司。
        except Exception as exc:  # noqa: BLE001 第三方公告采集隔离边界
            logger.warning('公司 %s 公告发现未完成: %s', symbol, exc)
            record['error'] = str(exc)
        record['discovered_count'] = len(company_documents)
        records.append(record)
        for identifier, document in company_documents.items():
            document['discovery_source'] = _QUERY
            document['query_scope_complete'] = record['status'] == 'query_scope_complete'
            all_documents[(symbol, identifier)] = document
        _save(root / 'checkpoint.json', {'as_of': as_of.isoformat(), 'companies': records,
                                        'documents': list(all_documents.values()), 'source_stopped': stopped, 'diagnostics': diagnostics,
                                        'rule_enabled': False, 'financial_verified': False})
    cache_bytes = sum(path.stat().st_size for path in root.glob('*.json') if path.is_file() and not path.is_symlink())
    warnings = []
    if cache_bytes > 1024 ** 3:
        warnings.append('公告发现缓存超过 1GiB 软上限，未自动删除')
        logger.warning('%s', warnings[-1])
    return {'as_of': as_of.isoformat(), 'companies': records, 'documents': list(all_documents.values()),
            'source_stopped': stopped, 'rule_enabled': False, 'financial_verified': False,
            'cache_bytes': cache_bytes, 'warnings': warnings, 'diagnostics': diagnostics}
