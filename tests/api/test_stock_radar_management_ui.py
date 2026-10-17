"""股票管理真实DOM交互契约，不使用字符串匹配代替行为。"""
import json
from pathlib import Path

from tests.api.test_static import _DOM_STUB, _run_node


def _script(body: str) -> str:
    url = Path('src/api/static/js/radar-stock-view.js').resolve().as_uri()
    return _DOM_STUB + '\n' + "import assert from 'node:assert/strict';\n" + f"const {{renderRadarStockPool}} = await import({json.dumps(url)});\n" + r"""
Element.prototype.append = function(...children) { children.forEach(child => this.appendChild(child)); };
const events = {};
window.addEventListener = (name, callback) => { (events[name] ||= []).push(callback); };
window.removeEventListener = (name, callback) => { events[name] = (events[name] || []).filter(item => item !== callback); };
let timers = new Map(), timerId = 0;
window.setTimeout = callback => { timers.set(++timerId, callback); return timerId; };
window.clearTimeout = id => timers.delete(id);
const tick = async () => { for(let i = 0; i < 12; i++) await Promise.resolve(); };
const find = (root, text) => descendants(root).find(el => el.tagName === 'BUTTON' && el.textContent === text);
const click = async (root, text) => { const b = find(root, text); assert.ok(b, text); assert.equal(b.disabled, false, text); await b.click(); await tick(); };
const root = makeElement('root');
const header = document.createElement('div');
const refresh = document.createElement('button'); refresh.className = 'radar-refresh'; header.appendChild(refresh);
const pool = { id: 'dividend_value', name: '红利价值' };
const empty = { items: [], unavailable_reason: '正式规则尚未启用' };
const company = { symbol: '600001', name: '测试公司', revision: 3, data_as_of: '2026-10-16' };
const detail = { company, evidence: [
 { evidence_id: 'hint', kind: 'research_hint', confirmable: false, payload: {name: '<script>bad</script>'}, source_url: 'javascript:alert(1)' },
 { evidence_id: 'fact', kind: 'financial_document', confirmable: true, payload: {value: 1}, source_url: 'https://example.org/document' }
], reviews: [], evaluation: {items: [{checks: [], pools: {dividend_value: {checks: []}}}]} };
const list = (page = 1) => ({items: [company], total: 31, page, page_size: 15, scope_status: 'complete'});
let calls = [], current = true;
""" + body


def test_stock_scope_search_pagination_and_detail_return(tmp_path):
    _run_node(tmp_path, _script(r"""
const request = async (url, options) => { calls.push([url, options]); if (url.includes('/updates/latest')) return null; if (url.includes('/companies/')) return detail; return list(Number(new URL('http://test' + url).searchParams.get('page'))); };
const dispose = renderRadarStockPool(root, header, pool, empty, {request, isCurrent: () => current}); await tick();
assert.ok(root.textContent.includes('尚无正式成员'));
await click(root, '候选与成员管理');
assert.ok(calls.at(-1)[0].includes('pool_id=dividend_value'));
await click(root, '下一页'); assert.ok(calls.at(-1)[0].includes('page=2'));
const search = byClass(root, 'stock-search')[0]; search.value = '测试';
await click(root, '搜索'); assert.ok(calls.at(-1)[0].includes('search=%E6%B5%8B%E8%AF%95&page=1'));
await click(root, '清除'); assert.ok(calls.at(-1)[0].includes('page=2'));
root.scrollTop = 91;
await click(root, '测试公司 · 600001'); assert.ok(root.textContent.includes('共同条件')); assert.ok(root.textContent.includes('红利价值条件'));
assert.equal(descendants(root).filter(el => el.tagName === 'A').length, 1);
await click(root, '返回初筛范围'); assert.ok(calls.at(-1)[0].includes('page=2')); assert.equal(root.scrollTop, 91);
dispose();
"""))


def test_stock_review_conflict_draft_and_unload_protection(tmp_path):
    _run_node(tmp_path, _script(r"""
let conflict = true, pendingResolve;
const request = async (url, options) => { calls.push([url, options]); if (url.includes('/updates/latest')) return null; if (options?.method === 'POST') { if (url.endsWith('/reviews') && conflict) { conflict = false; const e = new Error('revision changed'); e.status = 409; throw e; } if (url.endsWith('/draft')) return new Promise(resolve => pendingResolve = resolve); return {revision: 4}; } return url.includes('/companies/') ? detail : list(); };
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick();
await click(root, '候选与成员管理'); await click(root, '测试公司 · 600001');
const fill = async () => { const reason = byClass(root, 'stock-reason')[0]; reason.value = '需要核对原始报表'; await reason.dispatch('input'); const actor = byClass(root, 'stock-reviewer')[0]; actor.value = '用户'; await actor.dispatch('input'); };
await fill(); await click(root, '研究线索 · 待核验'); assert.equal(find(root, '确认事实').disabled, true);
await click(root, '财务原文 · 待核验'); assert.equal(find(root, '确认事实').disabled, false);
await click(root, '确认事实'); assert.ok(root.textContent.includes('资料版本已变化'));
const saved = JSON.parse(calls.find(([url]) => url.endsWith('/reviews'))[1].body); assert.equal(saved.expected_revision, 3); assert.equal(saved.evidence_id, 'fact'); assert.equal(saved.reviewer, '用户');
await click(root, '重新读取资料'); assert.equal(byClass(root, 'stock-reason')[0].value, '需要核对原始报表');
const unload = {preventDefault() {this.prevented = true;}}; events.beforeunload[0](unload); assert.equal(unload.prevented, true);
await click(root, '返回初筛范围'); await click(root, '测试公司 · 600001'); assert.equal(byClass(root, 'stock-reason')[0].value, '需要核对原始报表');
const draftButton = find(root, '保存关注草案'); const saving = draftButton.click(); await tick(); assert.equal(draftButton.disabled, true);
const draftCall = calls.find(([url]) => url.endsWith('/draft')); const body = JSON.parse(draftCall[1].body); assert.equal(body.pool_id, pool.id); assert.equal(body.expected_revision, 3); assert.ok(body.request_id); assert.equal(body.evaluation, undefined);
pendingResolve({status:'blocked', blockers:['规则未配置']}); await saving; await tick(); assert.ok(root.textContent.includes('仍受阻')); assert.equal(byClass(root, 'stock-reason')[0].value, '');
assert.equal(find(root, '新增 / 恢复正式成员').disabled, true); dispose();
"""))


def test_stock_task_poll_is_single_chain_and_stops_on_switch(tmp_path):
    _run_node(tmp_path, _script(r"""
let latest = 0;
const request = async (url, options) => { calls.push([url, options]); if (url.includes('/updates/')) { latest++; return latest === 1 ? null : {run_id:'task1', status:'running', phase:'collect'}; } return {run_id:'task1', status:'queued'}; };
const dispose = renderRadarStockPool(root, header, pool, empty, {request, isCurrent: () => current}); await tick();
await click(root, '准备候选'); assert.equal(timers.size, 1); assert.ok(calls.some(([url]) => url.endsWith('/updates/task1')));
const start = calls.find(([url]) => url.endsWith('/discover')); assert.deepEqual(JSON.parse(start[1].body), {pool_id: pool.id});
const timer = [...timers.values()][0]; timers.clear(); await timer(); await tick(); assert.equal(timers.size, 1);
current = false; const count = calls.length; const stop = [...timers.values()][0]; timers.clear(); await stop(); await tick(); assert.equal(calls.length, count); assert.equal(timers.size, 0); dispose();
"""))


def test_stock_late_response_and_retry(tmp_path):
    _run_node(tmp_path, _script(r"""
let fail = true, resolveList;
const request = async url => { if (url.includes('/updates/latest')) return null; if (fail) {fail = false; throw new Error('network down');} return new Promise(resolve => resolveList = resolve); };
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick(); await click(root, '候选与成员管理');
assert.ok(root.textContent.includes('network down')); await click(root, '重试');
renderRadarStockPool(root, header, {id:'growth_trend', name:'成长趋势'}, empty, {request: async () => null}); await tick();
resolveList(list()); await tick(); assert.ok(root.textContent.includes('成长趋势')); assert.equal(byClass(root, 'stock-company').length, 0); dispose();
"""))


def test_stock_checks_are_chinese_and_collapsed(tmp_path):
    _run_node(tmp_path, _script(r"""
detail.evaluation.items[0].checks = [{name:'annual_parent_profit_2023', outcome:'pending_verification', reason:'需要原文'}];
detail.evaluation.items[0].pools.dividend_value.unconfigured_rules = ['dividend_payout_ratio'];
const request = async url => url.includes('/updates/latest') ? null : url.includes('/companies/') ? detail : list();
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick();
await click(root, '候选与成员管理'); await click(root, '测试公司 · 600001');
assert.ok(root.textContent.includes('2023年归母净利润'));
assert.ok(root.textContent.includes('股利支付率'));
assert.ok(!root.textContent.includes('annual_parent_profit'));
const detailFold = descendants(root).find(el => el.tagName === 'DETAILS' && el.textContent.includes('查看核验明细'));
assert.ok(detailFold); assert.ok(!detailFold.open); dispose();
"""))


def test_hidden_stock_view_stops_poll_without_hash_change(tmp_path):
    _run_node(tmp_path, _script(r"""
const view = document.createElement('div'); view.className = 'view active'; root.closest = () => view;
const request = async (url, options) => { calls.push([url, options]); return url.includes('/updates/latest') ? null : {run_id:'task1',status:'running'}; };
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick();
await click(root, '准备候选'); assert.equal(timers.size, 1);
view.classList.remove('active'); const count = calls.length;
const timer = [...timers.values()][0]; timers.clear(); await timer(); await tick();
assert.equal(calls.length, count); assert.equal(timers.size, 0); dispose();
"""))
