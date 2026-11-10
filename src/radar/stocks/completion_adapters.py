"""待核验补全的三个数据适配器：年报现金流、成交额重采、来源重试。

适配器只负责把官方数据解析成可校验证据；不调用模型、不猜值、不直接改股票池。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

ADAPTER_VERSION = 'completion-adapters-2026-11-10-v1'
CASHFLOW_LABELS = {
    'netcash_operate': '经营活动产生的现金流量净额',
    'construct_long_asset': '购建固定资产、无形资产和其他长期资产支付的现金',
    'assign_dividend_profit': '分配股利、利润或偿付利息支付的现金',
    'end_cash': '期末现金及现金等价物余额',
}
_NUMBER = r'([-+]?[0-9][0-9,]*(?:\.[0-9]+)?)'


@dataclass
class AdapterResult:
    status: str
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ''
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {'status': self.status, 'evidence': self.evidence, 'reason': self.reason,
                'missing': self.missing, 'version': ADAPTER_VERSION}


def _number_after(text: str, label: str) -> float | None:
    compact = re.sub(r'[\s\u3000]+', '', text)
    match = re.search(re.escape(label) + r'.{0,40}?' + _NUMBER, compact)
    if not match:
        return None
    try:
        return float(match.group(1).replace(',', ''))
    except ValueError:
        return None


def parse_cashflow_text(text: str, year: int) -> dict[str, Any]:
    """从年报现金流量表文本提取四个字段；找不到的字段留在 missing。"""
    values = {key: _number_after(text, label) for key, label in CASHFLOW_LABELS.items()}
    missing = [key for key, value in values.items() if value is None]
    return {'year': year, 'fields': values, 'missing': missing,
            'status': 'complete' if not missing else 'pending_verification'}


class AnnualReportCashflowAdapter:
    """把年报现金流量表文本转成补全证据；由调用方提供原文抓取函数。"""
    kind = 'annual_report_cashflow'

    def collect(self, item: dict[str, Any], as_of: date,
                fetch: Callable[[str, list[int]], dict[int, str]] | None = None) -> AdapterResult:
        symbol = item.get('symbol')
        years = [int(value) for value in (item.get('gap') or {}).get('years') or []]
        if not isinstance(symbol, str) or not years:
            return AdapterResult('pending_verification', reason='缺少公司或年度')
        if fetch is None:
            return AdapterResult('pending_verification', reason='未配置年报原文抓取器')
        try:
            texts = fetch(symbol, years)
        except Exception as exc:  # noqa: BLE001 数据源边界
            return AdapterResult('pending_verification', reason=f'年报抓取失败：{type(exc).__name__}')
        per_year = {}
        for year in years:
            text = texts.get(year) if isinstance(texts, dict) else None
            if not isinstance(text, str) or not text.strip():
                per_year[str(year)] = {'status': 'pending_verification', 'missing': list(CASHFLOW_LABELS)}
                continue
            per_year[str(year)] = parse_cashflow_text(text, year)
        complete = all(value['status'] == 'complete' for value in per_year.values())
        missing = sorted({key for value in per_year.values() for key in value.get('missing', [])})
        evidence = {'symbol': symbol, 'as_of': as_of.isoformat(), 'years': per_year,
                    'source_kind': 'annual_report_cashflow'}
        return AdapterResult('complete' if complete else 'pending_verification', evidence=evidence,
                             reason='年报现金流字段完整' if complete else '年报现金流字段缺失或原文不可用',
                             missing=missing)


def parse_turnover_rows(rows: Any, as_of: date) -> dict[str, Any]:
    """校验 60 个交易日、日期升序、金额非负；返回可直接用于流动性判断的 payload。"""
    if not isinstance(rows, list) or len(rows) != 60:
        return {'status': 'pending_verification', 'reason': '成交额不是完整60日', 'daily': []}
    daily = []
    for row in rows:
        if not isinstance(row, dict):
            return {'status': 'pending_verification', 'reason': '成交额行格式错误', 'daily': []}
        raw_amount = row.get('amount_yuan')
        if not isinstance(raw_amount, (str, int, float)) or isinstance(raw_amount, bool):
            return {'status': 'pending_verification', 'reason': '成交额日期或金额无效', 'daily': []}
        try:
            day = date.fromisoformat(str(row.get('date'))[:10])
            amount = float(raw_amount)
        except (TypeError, ValueError):
            return {'status': 'pending_verification', 'reason': '成交额日期或金额无效', 'daily': []}
        if day > as_of or amount < 0:
            return {'status': 'pending_verification', 'reason': '成交额日期晚于截点或金额为负', 'daily': []}
        daily.append({'date': day.isoformat(), 'amount_yuan': str(amount)})
    days = [date.fromisoformat(row['date']) for row in daily]
    if len(set(days)) != 60 or any(days[index] >= days[index + 1] for index in range(59)):
        return {'status': 'pending_verification', 'reason': '成交额日期重复或未升序', 'daily': []}
    return {'status': 'complete', 'daily': daily, 'window_end': days[-1].isoformat()}


class TurnoverRetryAdapter:
    """重采 60 日成交额；由调用方提供抓取函数。"""
    kind = 'market_turnover_60d'

    def collect(self, item: dict[str, Any], as_of: date,
                fetch: Callable[[str, date], list[dict[str, Any]]] | None = None) -> AdapterResult:
        symbol = item.get('symbol')
        if not isinstance(symbol, str):
            return AdapterResult('pending_verification', reason='缺少公司代码')
        if fetch is None:
            return AdapterResult('pending_verification', reason='未配置成交额抓取器')
        try:
            rows = fetch(symbol, as_of)
        except Exception as exc:  # noqa: BLE001 数据源边界
            return AdapterResult('pending_verification', reason=f'成交额抓取失败：{type(exc).__name__}')
        parsed = parse_turnover_rows(rows, as_of)
        evidence = {'symbol': symbol, 'as_of': as_of.isoformat(), **parsed}
        return AdapterResult(parsed['status'], evidence=evidence, reason=parsed.get('reason', ''),
                             missing=[] if parsed['status'] == 'complete' else ['daily'])


class SourceRetryAdapter:
    """对失败来源做一次重试；由调用方提供 collector，返回 collection/result。"""
    kind = 'dividend_source_retry'

    def collect(self, item: dict[str, Any], as_of: date,
                retry: Callable[[str, date], dict[str, Any]] | None = None) -> AdapterResult:
        symbol = item.get('symbol')
        if not isinstance(symbol, str):
            return AdapterResult('pending_verification', reason='缺少公司代码')
        if retry is None:
            return AdapterResult('pending_verification', reason='未配置来源重试器')
        try:
            result = retry(symbol, as_of)
        except Exception as exc:  # noqa: BLE001 数据源边界
            return AdapterResult('pending_verification', reason=f'来源重试失败：{type(exc).__name__}')
        if not isinstance(result, dict):
            return AdapterResult('pending_verification', reason='来源重试返回格式错误')
        status = result.get('status')
        if status in {'complete', 'current'}:
            return AdapterResult('complete', evidence={'symbol': symbol, 'as_of': as_of.isoformat(), 'result': result},
                                 reason='来源重试成功')
        return AdapterResult('pending_verification', evidence={'symbol': symbol, 'result': result},
                             reason='来源重试后仍不完整', missing=['source'])


ADAPTERS = {
    'annual_report_cashflow': AnnualReportCashflowAdapter(),
    'market_turnover_60d': TurnoverRetryAdapter(),
    'dividend_source_retry': SourceRetryAdapter(),
}
