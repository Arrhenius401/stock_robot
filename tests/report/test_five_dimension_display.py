"""五维新口径在下载和共享网页保持一致，并保留旧报告语义。"""
import json

import pytest

from data.schemas import AnalysisResult
from report.builder import ReportBuilder
from tests.api.test_static import _DOM_STUB, _module_url, _run_node
from tests.report.test_peer_comparison import comparison_sample


def risk_metrics(risk=2):
    return {"sentiment_scoring_version": "risk-v1", "risk_score": risk,
            "participation_score": None if risk is None else 10-risk,
            "assessment_status": "failed" if risk is None else "complete",
            "assessment_reason": "模型标注超时" if risk is None else "轻微负面",
            "news_disclaimer": "关键词检索结果；不作AI筛选或情绪评分，不保证完整覆盖。",
            "evaluated_count": 0 if risk is None else 2,
            "window_start": "2026-09-26", "window_end": "2026-10-25",
            "all_items": [{"title": "<img src=x>公司公告", "publish_date": "2026-10-24",
                           "url": "https://example.com/news", "media": "来源甲", "evidence": "公告依据"},
                          {"title": "失败链接", "url": "javascript:alert(1)", "related": False, "severity": "major"}]}


@pytest.mark.parametrize("market", ["A 股", "港股"])
@pytest.mark.parametrize("risk", [0, 2, 5, 10, None])
def test_new_markdown_shows_positive_scores_and_original_sentiment_risk(market, risk):
    results = [AnalysisResult(dimension="industry", status="partial", summary="同行经营比较", score=7.2,
                    score_detail="完整年度与最新一期合成", metrics={"comparison": comparison_sample()}),
               AnalysisResult(dimension="sentiment", status="partial", summary="舆情依据", score=risk,
                    metrics=risk_metrics(risk))]
    markdown = ReportBuilder().build("00700.HK", "示例", results, {}, market=market,
        scoring_method="five-dimension-v1", base_score=7.2, final_score=7.2,
        score_rows=[{"label": "行业对比", "score": 7.2, "weight": "20%", "detail": "完整年度与最新一期合成"},
                    {"label": "舆情风险", "score": None if risk is None else 10-risk,
                     "weight": "5%", "direction": "risk", "raw_score": risk}])
    for value in ["five-dimension-v1", "综合参与分", "越高风险越大", "来源甲", "2026", "https://example.com/news",
                  "12.50%", "完整年度与最新一期合成", "缺失项目不记零分"]:
        assert value in markdown
    assert "行业对比不计分" not in markdown
    assert "港股不套用 A 股综合评分" not in markdown
    assert "<img src=x>" not in markdown
    assert "javascript:alert" not in markdown
    assert "与目标无直接关联" in markdown
    assert "不作AI筛选或情绪评分" not in markdown
    if risk is None:
        assert "模型标注超时" in markdown
    else:
        assert f"**舆情原风险分：** {risk:.1f}/10" in markdown
        assert f"**综合参与分：** {10-risk:.1f}/10" in markdown


def test_new_web_score_cards_and_legacy_semantics(tmp_path):
    script = _DOM_STUB + "\nconst {renderStockReport}=await import(" + json.dumps(
        _module_url("src/api/static/js/report-renderer.js")) + ");\n"
    script += "const comparison=" + json.dumps(comparison_sample(), ensure_ascii=False) + ";\n"
    script += "const metrics=" + json.dumps(risk_metrics(), ensure_ascii=False) + ";\n"
    script += r'''
for (const market of ['a-shares','hk']) {
 const report={market,scoring_method:'five-dimension-v1',score:{final:7.2,risk_deduction:8},
 score_rows:[{label:'行业对比',score:7.2,weight:'20%'},{label:'舆情风险',score:8,raw_score:2,weight:'5%',direction:'risk'},
 {label:'财务健康',score:null,weight:'30%',detail:'年度依据不足'}],
 dimensions:{industry:{score:7.2,status:'partial',score_detail:'完整年度与最新一期合成',metrics:{comparison}},
 sentiment:{score:2,status:'ok',metrics}}};
 const article=renderStockReport(report), score=descendants(article).find(n=>n.id==='report-score');
 if(!score.textContent.includes('7.2')||!score.textContent.includes('综合参与分'))throw new Error('评分语义缺失');
 if(score.textContent.includes('风险扣分')||article.textContent.includes('评分不适用'))throw new Error('新口径仍有旧限制');
 const industry=descendants(article).find(n=>n.id==='report-industry');
 if(!industry.textContent.includes('7.2')||!score.textContent.includes('完整年度与最新一期合成')||industry.textContent.includes('不计分'))throw new Error('行业无新分数依据');
 const news=descendants(article).find(n=>n.id==='report-sentiment');
 for(const value of ['2.0','越高风险越大','综合参与分 8.0','来源甲','2026-09-26','已评估2条','与目标无直接关联'])if(!news.textContent.includes(value))throw new Error('舆情缺少 '+value);
 const links=descendants(news).filter(n=>n.tagName==='A');
 if(links.length!==1||links[0].getAttribute('href')!=='https://example.com/news')throw new Error('安全来源链接未保留');
 if(descendants(news).some(n=>n.tagName==='IMG'))throw new Error('未转义标题');
 if(!score.textContent.includes('年度依据不足')||!score.textContent.includes('缺失项目不记零分'))throw new Error('缺项说明缺失');
 const failed=structuredClone(report);failed.dimensions.sentiment={status:'partial',score:null,metrics:{...metrics,risk_score:null,participation_score:null,assessment_reason:'模型标注超时',evaluated_count:0}};
 const failedText=renderStockReport(failed).textContent;
 if(!failedText.includes('模型标注超时')||!failedText.includes('无法评估')||!failedText.includes('公司公告'))throw new Error('失败缺因或隐藏成功列表');
}
const legacy=renderStockReport({market:'a-shares',score:{final:6},dimensions:{sentiment:{status:'ok',score:8,metrics:{headline_count:2}},industry:{score:null,status:'partial',metrics:{industry:'银行'}}}});
if(legacy.textContent.includes('综合参与分')||legacy.textContent.includes('越高风险越大'))throw new Error('旧正向舆情被重新解释');
if(!legacy.textContent.includes('不计分'))throw new Error('旧行业口径丢失');
'''
    _run_node(tmp_path, script)


def test_sentiment_sources_merge_all_original_hk_links_and_assessment_evidence():
    from report.formatter import format_sentiment_risk_markdown

    metrics = risk_metrics()
    metrics["news_items"] = [{"title": "<img src=x>公司公告", "published_at": "2026-10-24T12:00:00+08:00",
        "links": [{"url": "https://example.com/news", "media": "来源甲"},
                  {"url": "https://example.com/extra", "media": "来源乙"}]},
        {"title": "成功采集但不可评估", "published_at": "2026-10-23", "links": []}]
    markdown = format_sentiment_risk_markdown(metrics)
    for value in ["来源乙", "https://example.com/extra", "公告依据", "成功采集但不可评估"]:
        assert value in markdown


def test_new_web_all_risk_levels_missing_scores_and_hk_source_links(tmp_path):
    script = _DOM_STUB + "\nconst {renderStockReport}=await import(" + json.dumps(
        _module_url("src/api/static/js/report-renderer.js")) + ");\n"
    script += r'''
for (const market of ['a-shares','hk']) for(const risk of [0,2,5,10,null]) {
 const metrics={risk_score:risk,participation_score:risk===null?null:10-risk,evaluated_count:2,
 assessment_reason:risk===null?'标注失败':'有效样本',all_items:[{title:'原始新闻',evidence:'公告依据',url:'https://example.com/one'}],
 news_items:[{title:'原始新闻',published_at:'2026-10-24',links:[{url:'https://example.com/one',media:'来源甲'},{url:'https://example.com/two',media:'来源乙'}]},
 {title:'成功采集但不可评估',published_at:'2026-10-23',links:[]}]};
 const report={market,scoring_method:'five-dimension-v1',score:{final:null},
 score_rows:[{label:'财务',score:null,weight:'30%'},{label:'技术',score:'N/A',weight:'20%'},
 {label:'估值',score:null,weight:'25%'},{label:'行业',score:null,weight:'20%'},
 {label:'舆情',score:risk===null?'N/A':String(10-risk),weight:'5%',direction:'risk'}],dimensions:{sentiment:{metrics}}};
 const article=renderStockReport(report), score=descendants(article).find(n=>n.id==='report-score'), card=descendants(article).find(n=>n.id==='report-sentiment');
 if(!score.textContent.includes('暂无评分')||!score.textContent.includes('仅有舆情数据时不输出综合分'))throw new Error('只有舆情伪装总分');
 if(byClass(score,'score-row-missing').length!==(risk===null?5:4))throw new Error('缺失项未保留');
 const bars=byClass(score,'bar');
 if(bars.slice(0,4).some(bar=>bar.children[0].style.width!=='0%'))throw new Error('缺项图示异常');
 if(!card.textContent.includes('公告依据')||!card.textContent.includes('成功采集但不可评估')||!card.textContent.includes('来源乙'))throw new Error('源列表被标注子集隐藏');
 if(risk===null){if(!card.textContent.includes('无法评估')||card.textContent.includes('综合参与分 10.0'))throw new Error('失败冒充零风险');}
 else if(!card.textContent.includes(`综合参与分 ${(10-risk).toFixed(1)}`))throw new Error('反向值错误');
}
'''
    _run_node(tmp_path, script)


def test_sentiment_source_merge_never_attaches_other_dates_evidence():
    from report.formatter import sentiment_display_items

    metrics = {"all_items": [
        {"item_id": "n1", "title": "公司回购", "publish_date": "2026-10-24", "evidence": "24日依据"},
        {"item_id": "n2", "title": "公司回购", "publish_date": "2026-10-23", "evidence": "23日依据"}],
        "news_items": [
            {"title": "公司回购", "published_at": "2026-10-24T12:00:00+08:00", "links": []},
            {"title": "公司回购", "published_at": "2026-10-23T12:00:00+08:00", "links": []}]}
    items = sentiment_display_items(metrics)
    assert len(items) == 2
    assert [item["evidence"] for item in items] == ["24日依据", "23日依据"]


def test_partial_five_dimension_quality_preserves_valid_a_share_signal_and_hk_boundary(tmp_path):
    script = _DOM_STUB + "\nconst {renderStockReport}=await import(" + json.dumps(
        _module_url("src/api/static/js/report-renderer.js")) + ");\n"
    script += r'''
const base={scoring_method:'five-dimension-v1',score:{final:8},score_rows:[],
 data_quality:{status:'partial',signal_allowed:true,has_analysis_data:true,coverage:75},
 signal:{label:'积极观察',action:'保持关注',position:'20%'},dimensions:{}};
const article=renderStockReport({...base,market:'a-shares'});
const summary=descendants(article).find(n=>n.id==='report-summary');
if(!byClass(summary,'badge')[0].textContent.includes('数据不完整'))throw new Error('掩盖维度缺失');
const signal=byClass(article,'report-signal')[0];
if(!signal.textContent.includes('积极观察')||!signal.textContent.includes('生成依据'))throw new Error('有效信号或依据缺失');
if(signal.textContent.includes('20%')||signal.textContent.includes('保持关注'))throw new Error('旧动作仓位未移除');
const blocked=renderStockReport({...base,market:'a-shares',signal:null,data_quality:{...base.data_quality,signal_allowed:false}});
if(!byClass(blocked,'report-signal')[0]?.textContent.includes('暂未生成'))throw new Error('核心门槛不足未解释');
const detail=renderStockReport({...base,market:'a-shares',signal:null,data_quality:{...base.data_quality,signal_allowed:false,
 signal_unavailable_reason:'财务、估值分析依据未满足信号生成条件。'}});
if(!byClass(detail,'report-signal')[0]?.textContent.includes('财务、估值分析依据未满足信号生成条件'))throw new Error('具体原因丢失');
if(!byClass(blocked,'report-signal')[0]?.textContent.includes('当前报告未生成模型信号'))throw new Error('旧快照缺原因时仍需占位');
const hk=renderStockReport({...base,market:'hk',signal:null,data_quality:{...base.data_quality,signal_allowed:false}});
const hkSignal=byClass(hk,'report-signal')[0].textContent;
if(!hkSignal.includes('本期港股不提供模型信号')||hkSignal.includes('积极观察'))throw new Error('港股信号边界丢失');
'''
    _run_node(tmp_path, script)
