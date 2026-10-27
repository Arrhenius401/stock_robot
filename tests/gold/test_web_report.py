"""黄金共享卡片与报告库筛选的前端契约回归。"""

import json

from gold.report import render_gold_markdown
from tests.api.test_static import _DOM_STUB, _module_url, _run_node


def test_observation_report_prioritizes_archived_prices_and_chat_facts(tmp_path):
    script = (
        _DOM_STUB
        + r"""
const {renderStockReport,renderReportSummary}=await import(__RENDERER__);
const payload={report_type:'gold',report_schema:'gold-observation-v2',symbol:'GOLD',name:'黄金市场观察简报',score:{final:10},
 markets:{domestic:{latest_close:861.41,unit:'元/克',as_of:'2026-10-26',status:'ok',short_term:{direction:'偏空'}},
 international:{latest_close:3997.79,unit:'美元/金衡盎司',as_of:'2026-10-23',status:'stale',short_term:{direction:'中性'}}},
 markdown:'# 错误批次\n\n## 黄金价格\n\n错误价格'};
const archived='# 黄金市场观察简报\n\n## 黄金价格\n\n| 项目 | 价格 |\n| --- | --- |\n| 国内 | 861.41 元/克 |\n\n快速快照：国内技术偏空\n\n## 近期价格变化\n\n价格变化正文\n\n## 附录：完整依据\n\n归档依据';
const article=renderStockReport(payload,{sectionIdPrefix:'drawer-',archiveMarkdown:archived});
const html=descendants(article).map(n=>n.innerHTML).join('\n');
if(!article.children[0].textContent.includes('黄金市场观察简报')||!article.children[0].textContent.includes('黄金价格'))throw new Error('首屏缺少标题或价格');
for(const text of ['861.41 元/克','快速快照','价格变化正文','归档依据'])if(!html.includes(text))throw new Error('正文缺失：'+text);
if(html.includes('错误价格')||article.textContent.includes('长期：')||article.textContent.includes('投资摘要')||html.includes('暂无报告摘要'))throw new Error('混入旧结论或其他批次');
const summary=renderReportSummary({payload});
for(const text of ['861.41','元/克','2026-10-26','偏空','3,997.79','美元/金衡盎司','2026-10-23','无法判断','旧数据'])if(!summary.textContent.includes(text))throw new Error('摘要缺失：'+text);
if(summary.textContent.includes('技术中性'))throw new Error('过期行情错误显示技术方向');
if(summary.textContent.includes('长期：')||byClass(summary,'report-summary-score').length)throw new Error('误显示旧结论或股票评分');
payload.markets.domestic={latest_close:0,short_term:{direction:'<script>伪造</script>'}};
const missing=renderReportSummary({payload});
if(!missing.textContent.includes('价格不可用')||!missing.textContent.includes('日期未记录'))throw new Error('缺失数据未明确展示');
if(descendants(missing).some(n=>n.innerHTML.includes('<script>')))throw new Error('方向成为 HTML 结构');
""".replace("__RENDERER__", json.dumps(_module_url("src/api/static/js/report-renderer.js")))
    )
    _run_node(tmp_path, script)


def test_gold_renderer_uses_markdown_cards_and_plain_direction_summary(tmp_path):
    script = (
        _DOM_STUB
        + r"""
const {renderStockReport,renderReportSummary}=await import(__RENDERER__);
const payload={report_type:'gold',symbol:'GOLD',name:'黄金综合研报',
 score:{final:10},long_term:{direction:'看空'},markets:{
 domestic:{short_term:{direction:'看多（强）'}},international:{short_term:{direction:'偏空'}}},
 markdown:'# 黄金综合研报\n\n生成时间：归档时间\n\n## 国内黄金\n\n国内完整正文\n\n## 国际黄金\n\n国际完整正文'};
const article=renderStockReport(payload,{sectionIdPrefix:'drawer-',archiveMarkdown:payload.markdown});
if(!article.classList.contains('stock-report')||!article.classList.contains('gold-report'))throw new Error('黄金未复用报告卡片');
if(byClass(article,'report-score').length||byClass(article,'report-quality').length)throw new Error('黄金误走股票评分或空维度');
const summary=renderReportSummary({artifact_id:'gold-id',payload});
if(byClass(summary,'report-summary-score').length)throw new Error('黄金不得显示股票评分');
for(const text of ['长期：看空','国内短期：看多（强）','国际短期：偏空']){
 if(!article.textContent.includes(text)||!summary.textContent.includes(text))throw new Error('黄金摘要方向缺失：'+text);
}
const html=descendants(article).map(n=>n.innerHTML).join('\n');
if(!html.includes('国内完整正文')||!html.includes('国际完整正文'))throw new Error('Markdown正文丢失');
payload.long_term.direction='<script>伪造</script> **看多**';
const hostile=renderReportSummary({payload});
if(descendants(hostile).some(n=>n.innerHTML.includes('<script>')||n.innerHTML.includes('<strong>看多')))throw new Error('摘要方向成为结构');
""".replace(
            "__RENDERER__",
            json.dumps(_module_url("src/api/static/js/report-renderer.js")),
        )
    )
    _run_node(tmp_path, script)


def test_gold_library_filter_and_snapshot_details(tmp_path):
    script = (
        _DOM_STUB
        + r"""
const root=makeElement('reportLibraryContent');
const {api}=await import(__API__);
const {bus}=await import(__STATE__);
const {initReportLibrary}=await import(__LIBRARY__);
let queryType;
api.listReports=async(options)=>{queryType=options.type;return {total:1,reports:[{id:'gold',type:'gold',title:'黄金综合研报'}]};};
let payload={report_type:'gold',symbol:'GOLD',name:'黄金综合研报',long_term:{direction:'看多'},markets:{domestic:{short_term:{direction:'偏多'}},international:{short_term:{direction:'中性'}}},markdown:'# 错误批次正文'};
api.getReport=async()=>({report:{id:'gold',type:'gold',title:'黄金综合研报'},markdown:'# 黄金综合研报\n\n## 国内黄金\n\n归档正文',gold_payload:payload});
api.downloadReportUrl=()=>'/download/gold';
initReportLibrary();
const event=new Event('view-change');Object.defineProperty(event,'detail',{value:{view:'report-library'}});bus.dispatchEvent(event);
await new Promise(resolve=>setTimeout(resolve,0));
const filter=descendants(root).find(n=>n.tagName==='BUTTON'&&n.textContent==='黄金');
if(!filter)throw new Error('缺少黄金分类');
await filter.click();await new Promise(resolve=>setTimeout(resolve,0));
if(queryType!=='gold')throw new Error('筛选未请求gold');
await byClass(root,'report-library-open')[0].click();
if(!root.textContent.includes('长期：看多')||!byClass(root,'gold-report').length)throw new Error('未使用黄金快照');
let html=descendants(root).map(n=>n.innerHTML).join('\n');
if(!html.includes('归档正文')||html.includes('错误批次正文'))throw new Error('未优先使用绑定SHA的正文');
if(byClass(root,'report-library-metrics').length)throw new Error('黄金出现回测指标');
const buttons=()=>descendants(root).filter(n=>n.tagName==='BUTTON');
await buttons().find(n=>n.textContent==='Markdown 原文').click();
if(!byClass(root,'report-library-markdown')[0].innerHTML.includes('归档正文'))throw new Error('原文入口丢失');
await byClass(root,'report-library-back')[0].click();
payload={report_type:'gold',report_schema:'gold-observation-v2',name:'黄金市场观察简报',markets:{},markdown:'# 错误批次正文'};
await byClass(root,'report-library-open')[0].click();
html=descendants(root).map(n=>n.innerHTML).join('\n');
if(!html.includes('归档正文')||html.includes('错误批次正文')||root.textContent.includes('长期：'))throw new Error('新版未使用归档正文或混入旧结论');
await byClass(root,'report-library-back')[0].click();payload=null;
await byClass(root,'report-library-open')[0].click();
html=descendants(root).map(n=>n.innerHTML).join('\n');
if(!html.includes('归档正文'))throw new Error('缺失快照没有完整回退');
"""
    )
    for token, path in {
        "__API__": "src/api/static/js/api.js",
        "__STATE__": "src/api/static/js/state.js",
        "__LIBRARY__": "src/api/static/js/report-library.js",
    }.items():
        script = script.replace(token, json.dumps(_module_url(path)))
    _run_node(tmp_path, script)


def test_generated_gold_markdown_keeps_untrusted_text_literal_in_web_parser(tmp_path):
    attack = "<script>伪造</script> |额外列| **强制建议** [链接](https://evil.test)"
    markdown = render_gold_markdown(
        {
            "notes": [attack],
            "reading_guide": "1. 注入列表",
            "commentary_status": "ok",
            "commentary": "---",
        }
    )
    script = (
        _DOM_STUB
        + r"""
const {renderStockReport}=await import(__RENDERER__);
const article=renderStockReport({report_type:'gold',markdown:__MARKDOWN__});
const html=descendants(article).map(n=>n.innerHTML).join('\n');
if(html.includes('<script>')||html.includes('<strong>强制建议</strong>')||html.includes('href="https://evil.test'))throw new Error('报告上游文本被解释为结构');
if(html.includes('<ol>')||html.includes('<hr>'))throw new Error('独立正文产生列表或分隔线');
""".replace(
            "__RENDERER__",
            json.dumps(_module_url("src/api/static/js/report-renderer.js")),
        ).replace("__MARKDOWN__", json.dumps(markdown))
    )
    _run_node(tmp_path, script)
