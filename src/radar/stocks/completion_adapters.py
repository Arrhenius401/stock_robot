"""待核验补全适配器：只把现有来源结果整理成统一证据，不另建抓取/校验通道。

- 年报现金流：消费已归档年报的现金流量表抽取结果；
- 成交额：消费已校验的 turnover_source payload；
- 不提供来源重试、不提供本地证据目录、不重复实现来源校验。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from radar.stocks.financial_extract import extract_cashflow_text

ADAPTER_VERSION = 'completion-adapters-2026-11-11-v2'


@dataclass
class AdapterResult:
    status: str
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ''
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {'status': self.status, 'evidence': self.evidence, 'reason': self.reason,
                'missing': self.missing, 'version': ADAPTER_VERSION}


class AnnualReportCashflowAdapter:
    """年报现金流：provider 返回 {year: {"fields": {...}} 或 年报文本}。"""
    kind = 'annual_report_cashflow'
    provider_key = 'annual_report'

    def collect(self, item: dict[str, Any], as_of: date,
                providers: dict[str, Callable[..., Any]] | None = None) -> AdapterResult:
        symbol = item.get('symbol')
        years = [int(value) for value in (item.get('gap') or {}).get('years') or []]
        if not isinstance(symbol, str) or not years:
            return AdapterResult('pending_verification', reason='缺少公司或年度')
        provider = (providers or {}).get(self.provider_key)
        if not callable(provider):
            return AdapterResult('pending_verification', reason='未配置年报归档/抽取器')
        try:
            source = provider(symbol, item.get('name'), years)
        except Exception as exc:  # noqa: BLE001 数据源边界
            return AdapterResult('pending_verification', reason=f'年报归档/抽取失败：{type(exc).__name__}')
        per_year: dict[str, Any] = {}
        for year in years:
            value = source.get(year) if isinstance(source, dict) else None
            if isinstance(value, dict):
                raw_fields = value.get('fields')
                fields = raw_fields if isinstance(raw_fields, dict) else value
                missing = [key for key in ('netcash_operate', 'construct_long_asset',
                                           'assign_dividend_profit', 'end_cash')
                           if fields.get(key) is None]
                per_year[str(year)] = {'status': 'complete' if not missing else 'pending_verification',
                                       'fields': fields, 'missing': missing}
            elif isinstance(value, str) and value.strip():
                parsed = extract_cashflow_text(value)
                per_year[str(year)] = parsed
            else:
                per_year[str(year)] = {'status': 'pending_verification',
                                       'fields': {}, 'missing': ['netcash_operate', 'construct_long_asset',
                                                                 'assign_dividend_profit', 'end_cash']}
        complete = all(value['status'] == 'complete' for value in per_year.values())
        missing = sorted({key for value in per_year.values() for key in value.get('missing', [])})
        evidence = {'symbol': symbol, 'as_of': as_of.isoformat(), 'years': per_year,
                    'source_kind': 'annual_report_cashflow'}
        return AdapterResult('complete' if complete else 'pending_verification', evidence=evidence,
                             reason='年报现金流字段完整' if complete else '年报现金流字段缺失',
                             missing=missing)


class TurnoverRetryAdapter:
    """成交额：provider 返回 turnover_source 已校验的 payload；不在适配器里再造校验器。"""
    kind = 'market_turnover_60d'
    provider_key = 'turnover'

    def collect(self, item: dict[str, Any], as_of: date,
                providers: dict[str, Callable[..., Any]] | None = None) -> AdapterResult:
        symbol = item.get('symbol')
        if not isinstance(symbol, str):
            return AdapterResult('pending_verification', reason='缺少公司代码')
        provider = (providers or {}).get(self.provider_key)
        if not callable(provider):
            return AdapterResult('pending_verification', reason='未配置成交额来源')
        try:
            payload = provider(symbol, as_of)
        except Exception as exc:  # noqa: BLE001 数据源边界
            return AdapterResult('pending_verification', reason=f'成交额来源失败：{type(exc).__name__}')
        daily = payload.get('daily') if isinstance(payload, dict) else None
        if not isinstance(daily, list) or len(daily) != 60:
            return AdapterResult('pending_verification', reason='成交额来源不完整', missing=['daily'])
        evidence = {'symbol': symbol, 'as_of': as_of.isoformat(), 'daily': daily,
                    'window_days': payload.get('window') or payload.get('window_days'),
                    'source_kind': 'market_turnover_60d'}
        return AdapterResult('complete', evidence=evidence, reason='成交额来源已校验')


ADAPTERS: dict[str, AnnualReportCashflowAdapter | TurnoverRetryAdapter] = {
    'annual_report_cashflow': AnnualReportCashflowAdapter(),
    'market_turnover_60d': TurnoverRetryAdapter(),
}
