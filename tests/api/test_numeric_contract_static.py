"""网页与 Python 使用同一组字段口径断言，避免两端漂移。"""
import json
import math

from tests.api.test_static import _DOM_STUB, _module_url, _run_node
from tests.report.test_numeric_contract import DISPLAY_CASES


def test_web_numeric_contract_and_report_outputs(tmp_path):
    cases = [row for row in DISPLAY_CASES if not isinstance(row[1], float) or math.isfinite(row[1])]
    script = _DOM_STUB + "\nconst {formatMetricValue,renderStockReport}=await import(" + json.dumps(
        _module_url("src/api/static/js/report-renderer.js")) + ");\n"
    script += "const cases=" + json.dumps(cases, ensure_ascii=False) + ";\n"
    script += r'''
for(const [key,value,expected] of cases){
  const actual=formatMetricValue(key,value);
  if(actual!==expected) throw new Error(`${key}: ${actual} !== ${expected}`);
}
if(formatMetricValue('roe',NaN)!=='数据异常') throw new Error('异常数值漏出');
const article=renderStockReport({market:'hk',overview:{latest_close:0.0012,change_pct:0},
  dimensions:{financial:{status:'partial',metrics:{revenue:3000000,roe:-0.0956,
    roe_trend:[{quarter:'2026-06-30',roe:-0.0956}]}},
  valuation:{status:'partial',metrics:{pe_ttm:-32.03,pb:1.2}}}});
const text=article.textContent;
for(const expected of ['300.00 万','-9.56%','0.00%','0.0012','不适用于高低估判断','1.20']){
  if(!text.includes(expected)) throw new Error('缺少展示值 '+expected+': '+text);
}
'''
    _run_node(tmp_path, script)
