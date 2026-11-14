"""结构化解读显示、失败与不可信文本的DOM回归。"""
import json

from tests.api.test_static import _DOM_STUB, _module_url, _run_node


def test_session_artifact_keeps_research_evidence():
    from api.app import _extract_stock_reports

    research = {"version": 2, "business_profile": {"source": "原始来源"}, "evidence": {"financial": {"status": "ok"}}}
    report = {"symbol": "000338", "research_commentary": research}
    extracted = _extract_stock_reports([{"tool": "analyze_stock", "status": "done", "content": report}])
    assert extracted[0][1]["research_commentary"] == research


def test_research_fields_and_failure_keep_rules(tmp_path):
    script = _DOM_STUB + r'''
const {renderStockReport}=await import(__RENDERER__);
const report={market:'a-shares',commentary_status:'ok',commentary:'不得重复显示的长文',
 research_commentary:{version:2,business_profile:{main_business:'发动机',source:'https://basic.10jqka.com.cn/',fetched_at:'2026-11-14'},
 model:{business:'产品介绍<script>bad()</script>',conclusion:{overall:'整体判断正文',support:'支撑正文',question:'疑问正文'},risks:[{text:'风险正文'}]},
 dimensions:[{label:'财务健康',text:'现金流存在分歧'}],limitation:'有限样本'}};
let article=renderStockReport(report);
let card=article.children.find(n=>n.id==='report-commentary');
if(!card.textContent.includes('整体判断正文')||!card.textContent.includes('现金流存在分歧'))throw Error('结构化正文缺失');
if(card.textContent.includes('不得重复显示的长文'))throw Error('重复展示bulk');
if(descendants(card).some(n=>n.tagName==='SCRIPT'||n.innerHTML.includes('<script>')))throw Error('模型文本被解析为HTML');
report.commentary_status='failed';
article=renderStockReport(report,{onRetryCommentary:()=>{}});
card=article.children.find(n=>n.id==='report-commentary');
if(card.textContent.includes('整体判断正文')||card.textContent.includes('风险正文'))throw Error('失败仍展示AI结论');
if(!card.textContent.includes('现金流存在分歧')||!card.textContent.includes('发动机'))throw Error('失败丢失规则与资料');
if(!descendants(card).some(n=>n.tagName==='BUTTON'))throw Error('重试入口丢失');
'''.replace('__RENDERER__', json.dumps(_module_url('src/api/static/js/report-renderer.js')))
    _run_node(tmp_path, script)


def test_current_price_prefers_quote_and_keeps_quote_timestamp(tmp_path):
    script = _DOM_STUB + r'''
const {renderStockReport}=await import(__RENDERER__);
const article=renderStockReport({market:'a-shares',overview:{industry:'汽车',latest_close:18.75,
 current_price:{value:19.15,kind:'quote',note:'行情时间：2026-11-16 10:00:00；来源：新浪行情'}}});
const quote=descendants(article).find(x=>x.classList.contains('report-latest-close'));
if(!quote.textContent.includes('当前价格：19.15')||!quote.textContent.includes('10:00:00')||!quote.textContent.includes('新浪行情'))throw Error('最新报价未展示');
if(quote.textContent.includes('18.75'))throw Error('错误显示日线收盘价');
'''.replace('__RENDERER__', json.dumps(_module_url('src/api/static/js/report-renderer.js')))
    _run_node(tmp_path, script)
