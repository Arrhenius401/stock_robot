"""补全回灌：消费 completion-run，按统一规则更新评估明细并写 overrides。

不再手工改 evaluation-details.csv。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

RECHECK_VERSION = 'completion-recheck-2026-11-11-v1'
_TURNOVER_FLOOR = 60 * 100000000


def _turnover_result(evidence: dict[str, Any]) -> tuple[str, str] | None:
    daily = evidence.get('daily')
    if not isinstance(daily, list) or len(daily) != 60:
        return None
    try:
        total = sum(float(row['amount_yuan']) for row in daily if isinstance(row, dict))
    except (TypeError, ValueError, KeyError):
        return None
    result = 'passed' if total >= _TURNOVER_FLOOR else 'failed'
    return result, f'60日成交额合计{total / 100000000:.2f}亿元'


def _cashflow_result(evidence: dict[str, Any]) -> tuple[str, str] | None:
    years = evidence.get('years')
    if not isinstance(years, dict) or not years:
        return None
    ocf = outflow = 0.0
    end_cash = []
    for value in years.values():
        fields = value.get('fields') if isinstance(value, dict) else None
        if not isinstance(fields, dict):
            return None
        try:
            ocf += float(fields['netcash_operate'])
            outflow += float(fields['assign_dividend_profit'])
            end_cash.append(float(fields['end_cash']))
        except (TypeError, ValueError, KeyError):
            return None
    if any(value < 0 for value in end_cash):
        return 'failed', '期末现金为负'
    result = 'passed' if ocf >= outflow else 'failed'
    return result, f'三年OCF {ocf / 100000000:.2f}亿元，分红及利息 {outflow / 100000000:.2f}亿元'


def recheck_run(run_path: Path, details_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    run = json.loads(run_path.read_text(encoding='utf-8'))
    items = run.get('items') if isinstance(run, dict) else None
    if not isinstance(items, list):
        raise TypeError('completion-run 格式无效')
    with details_path.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
        fields = list(rows[0].keys()) if rows else []
    if not fields:
        raise ValueError('评估明细为空')
    overrides = []
    for item in items:
        if not isinstance(item, dict) or item.get('status') != 'complete':
            continue
        symbol, condition = item.get('symbol'), item.get('condition')
        kind = item.get('source_kind')
        evidence = item.get('evidence')
        if not isinstance(evidence, dict):
            continue
        if kind == 'annual_report_cashflow':
            judged = _cashflow_result(evidence)
        elif kind == 'market_turnover_60d':
            judged = _turnover_result(evidence)
        else:
            judged = None
        if judged is None:
            continue
        result, reason = judged
        for row in rows:
            if row.get('代码') == symbol and row.get('条件') == condition:
                row['结果'] = result
                row['原因'] = reason
        overrides.append({'symbol': symbol, 'condition': condition, 'result': result, 'reason': reason})
    with details_path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    payload = {'version': RECHECK_VERSION, 'as_of': run.get('as_of'), 'overrides': overrides,
               'members_published': 0}
    if output_path is not None:
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return payload
