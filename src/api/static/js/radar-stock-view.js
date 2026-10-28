// 股票研究管理：资料、评估与正式成员保持分离。
const API = '/api/v1/radar/stocks';
const sessions = new Map();
let activeDispose = null;
let unloadBound = false;
const labels = { pending_verification: '待核验', confirmed: '已确认事实', rejected: '已否定', needs_evidence: '需补充证据', expired: '已过期', passed: '通过', failed: '未通过', unavailable: '暂无数据', unconfigured: '待配置', blocked: '受阻', queued: '排队中', running: '执行中', completed: '已完成', partial: '部分完成', cancelled: '已取消', active: '有效', effective: '有效', suspended: '暂停', research_hint: '研究线索', not_prepared: '尚未准备', complete: '完整', snapshot: '读取资料快照', collect: '采集资料', review: '整理待核验资料', finalizing: '归档结果' };
Object.assign(labels, { downloaded: '已下载', cached: '已缓存', source_error: '来源异常', source_stopped: '来源暂停', financial_document: '财务原文', financial_proposal: '财务线索', document: '分红原文', dividend_proposal: '分红线索', quarterly_financial_document: '季度财务原文', quarterly_financial_proposal: '季度财务线索', unresolved: '待解决事项', pending_configuration: '待配置', observe: '观察', excluded: '不符合', unsupported: '尚未配置来源' });
function label(value) { return labels[value] || '待核验'; }
const checkLabels = { security: '证券身份', listing_age: '上市年限', capitalization_current: '当前公司总市值', capitalization_average_60d: '60日平均公司总市值', ttm_parent_profit: '最近十二个月归母净利润', ttm_deducted_profit: '最近十二个月扣非净利润', audit: '审计意见', governance: '治理风险', leadership: '行业龙头证据', financial_quality: '财务质量', cash_ratio: '经营现金流与归母利润比率', cash_review: '现金质量核验', dividend_history: '连续分红', liquidity: '成交活跃度', dividend_payout_ratio: '股利支付率', dividend_yield: '股息率', value_valuation: '价值估值', growth_revenue: '营业收入增长', growth_profit: '利润增长', trend: '价格趋势' };
function checkLabel(value) { const annual = /^annual_(parent|deducted)_profit_(\d{4})$/.exec(value || ''); return annual ? annual[2] + '年' + (annual[1] === 'parent' ? '归母净利润' : '扣非净利润') : checkLabels[value] || '待核验项目'; }
Object.assign(labels, { attention: '需关注', not_configured: '待配置' });
Object.assign(checkLabels, { growth_deducted_profit: '扣非净利润增长', valuation: '估值', style_rules: '风格条件' });
function node(tag, cls = '', text = '') { const el = document.createElement(tag); el.className = cls; el.textContent = text; return el; }
function append(el, ...children) { children.forEach(child => el.appendChild(child)); return el; }
function button(text, action, cls = '') { const el = node('button', 'subs-btn ' + cls, text); el.type = 'button'; el.addEventListener('click', action); return el; }
function fold(title, child) { return append(node('details', 'stock-fold'), node('summary', '', title), child); }
function raw(value) { return node('pre', 'stock-raw', JSON.stringify(value, null, 2)); }
async function defaultRequest(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { const error = new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data.message || response.status)); error.status = response.status; throw error; }
  return data;
}
function post(body) { return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }; }
function hasDrafts() { return [...sessions.values()].some(s => Object.values(s.drafts).some(d => d.reason.trim())); }

export function renderRadarStockPool(root, header, pool, payload, options = {}) {
  if (activeDispose) activeDispose();
  if (!unloadBound) { window.addEventListener('beforeunload', event => { if (hasDrafts()) { event.preventDefault(); event.returnValue = ''; } }); unloadBound = true; }
  const request = options.request || defaultRequest;
  const scrollHost = root.closest('.view') || root;
  const state = sessions.get(pool.id) || { view: 'members', search: '', page: 1, unfilteredPage: 1, symbol: null, drafts: {}, scroll: 0, detailScroll: {} };
  sessions.set(pool.id, state);
  const content = node('section', 'panel radar-stock-empty stock-management');
  const taskBox = node('div', 'stock-task');
  const taskUI = options.taskUI;
  taskBox.hidden = !!taskUI;
  const body = node('div', 'stock-body');
  const notice = node('p', 'radar-note stock-notice'); notice.setAttribute('role', 'status'); notice.hidden = true;
  let disposed = false, timer = null, serial = 0, pollGeneration = 0, taskBusy = false, observer = null;
  const current = () => !disposed && root.contains(content) && (scrollHost === root || scrollHost.classList.contains('active')) && (!options.isCurrent || options.isCurrent());
  function dispose() { disposed = true; pollGeneration++; if (timer !== null) window.clearTimeout(timer); timer = null; observer?.disconnect(); window.removeEventListener?.('hashchange', onHash); }
  function onHash() { if (!current()) dispose(); }
  activeDispose = dispose;
  window.addEventListener('hashchange', onHash);
  const head = append(node('div', 'radar-pool-content-head'), node('h2', '', pool.name), button('候选与成员管理', () => { state.view = 'candidates'; state.symbol = null; show(); }, 'stock-manage'));
  const prepare = button('准备候选', () => startTask('/discover', { pool_id: pool.id }), 'stock-prepare');
  append(content, head, taskBox, notice, body);
  root.replaceChildren(header, content);
  if (typeof MutationObserver !== 'undefined') { observer = new MutationObserver(() => { if (!current()) dispose(); }); observer.observe(root, { childList: true }); }
  const refresh = header.querySelector('.radar-refresh');
  if (taskUI) { const finance = button('财务复核', () => taskUI.confirmFinancial(pool).catch(messageError), 'stock-financial-review'); const controls = header.querySelector('.radar-update-actions'); if (controls) controls.prepend(finance); else header.append(finance); }
  function messageError(error) { message('任务未能启动：' + error.message); }
  if (refresh) refresh.onclick = () => startTask('/' + encodeURIComponent(pool.id) + '/update', {});
  function message(text) { notice.textContent = text; notice.hidden = !text; }
  function errorBox(error, retry) {
    const box = append(node('div', 'radar-note stock-error'), node('p', '', '读取失败：' + error.message), button('重试', retry, 'stock-retry'));
    body.replaceChildren(box);
  }
  function busyButtons(value) { taskBusy = value; prepare.disabled = value; if (refresh) refresh.disabled = value || !(payload.items || []).some(item => ['active', 'effective', 'suspended'].includes(item.status)); }
  function drawTask(task) {
    taskBox.replaceChildren();
    const run = task?.task || task?.run || task;
    if (!run) { busyButtons(false); return false; }
    const wasBusy = taskBusy;
    const running = ['queued', 'running'].includes(run.status);
    busyButtons(running);
    const box = append(node('div', 'radar-note'), node('p', '', '资料任务 · ' + label(run.status) + ((run.phase || run.stage) ? ' · ' + label(run.phase || run.stage) : '')), node('p', 'radar-meta', running ? '任务在后台继续，切换页面不会取消。' : '资料发现完成不代表通过资格核验。'));
    if (run.error || run.error_summary) append(box, node('p', '', run.error || run.error_summary));
    const sources = node('div', 'stock-task-sources');
    (run.results || []).forEach(item => append(sources, node('p', 'stock-check', item.scope + ' · ' + label(item.status) + ' · ' + (typeof item.summary === 'string' ? item.summary : JSON.stringify(item.summary || {})))));
    append(box, fold('逐源结果与任务详情', append(sources, raw(run))));
    append(taskBox, box);
    if (wasBusy && !running && state.view === 'candidates') show();
    return running;
  }
  async function poll(runId = null) {
    const generation = ++pollGeneration;
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    try {
      const task = await request(API + (runId ? '/updates/' + encodeURIComponent(runId) : '/updates/latest?pool_id=' + encodeURIComponent(pool.id)));
      if (!current() || generation !== pollGeneration) return;
      const run = task?.task || task?.run || task;
      if (drawTask(task)) timer = window.setTimeout(() => { timer = null; if (current()) poll(run?.run_id); else dispose(); }, 2000);
    } catch (error) {
      if (!current() || generation !== pollGeneration) return;
      taskBox.replaceChildren(append(node('div', 'radar-note'), node('p', '', '任务状态读取失败：' + error.message), button('重新读取任务', () => poll(runId))));
      busyButtons(false);
    }
  }
  async function startTask(path, data) {
    if (!current()) return;
    if (taskUI) { try { await taskUI.start(pool, path === '/discover' ? 'discover_candidates' : 'refresh_members'); } catch (error) { messageError(error); } return; }
    if (taskBusy) return;
    busyButtons(true); message('');
    pollGeneration++; if (timer !== null) window.clearTimeout(timer); timer = null;
    try { const task = await request(API + path, post(data)); if (!current()) return; drawTask(task); await poll((task?.task || task?.run || task)?.run_id); }
    catch (error) { if (!current()) return; busyButtons(false); message('任务未能启动：' + error.message); }
  }
  function nav() {
    return append(node('div', 'stock-toolbar'), button('返回成员', () => { state.view = 'members'; state.symbol = null; show(); }), button('初筛范围', () => { state.view = 'candidates'; state.symbol = null; show(); }), button('关注草案', () => { state.view = 'drafts'; state.historyPage = 1; show(); }), button('成员变更历史', () => { state.view = 'events'; state.historyPage = 1; show(); }));
  }
  function pager(target, data, change) {
    const bar = node('div', 'stock-pagination');
    const previous = button('上一页', () => change(Math.max(1, data.page - 1)), 'stock-prev'); previous.disabled = data.page <= 1;
    const next = button('下一页', () => change(data.page + 1), 'stock-next'); next.disabled = data.page * (data.page_size || 15) >= data.total;
    append(bar, previous, node('span', 'radar-meta', '第 ' + data.page + ' 页 · 共 ' + data.total + ' 项'), next); append(target, bar);
  }
  function showMembers() {
    const items = (payload.items || []).filter(item => ['active', 'effective', 'suspended'].includes(item.status));
    body.replaceChildren();
    if (!items.length) append(body, node('h3', '', '尚无正式成员'), node('p', 'radar-meta', '审核发布后，这里将按行业展示你的关注名单。'));
    else {
      const groups = new Map();
      items.forEach(item => { const industry = item.industry_level1 || '行业待核验'; if (!groups.has(industry)) groups.set(industry, []); groups.get(industry).push(item); });
      groups.forEach((rows, industry) => {
        const group = append(node('details', 'stock-fold'), node('summary', '', industry + ' · ' + rows.length + ' 家')); group.open = items.length <= 15;
        rows.forEach(item => append(group, node('p', 'stock-row', (item.name || item.symbol) + ' ' + item.symbol + ' · ' + label(item.status) + ' · 机会状态：暂无数据'))); append(body, group);
      });
    }
    append(body, node('p', 'radar-note', payload.unavailable_reason || '正式选股规则尚未启用；准备资料不授予入池资格。'), prepare);
  }
  async function showCandidates(token) {
    body.replaceChildren(nav(), node('h3', '', '初筛范围'), node('p', 'radar-meta', '当前池指数成分并集，仅为研究范围，尚未完成资格核验。'));
    const form = node('form', 'stock-toolbar stock-search-form');
    const input = node('input', 'stock-search'); input.type = 'search'; input.placeholder = '搜索代码或公司名称'; input.setAttribute('aria-label', '搜索代码或公司名称'); input.value = state.search;
    const submit = button('搜索', () => search());
    function search() { if (!state.search) state.unfilteredPage = state.page; state.search = input.value.trim(); state.page = 1; show(); }
    form.addEventListener('submit', event => { event.preventDefault(); search(); });
    append(form, input, submit, button('清除', () => { state.search = ''; state.page = state.unfilteredPage; show(); }, 'stock-clear'));
    const results = node('div', 'stock-results', '正在读取初筛范围…'); append(body, form, results);
    try {
      const data = await request(API + '/candidates?pool_id=' + encodeURIComponent(pool.id) + '&search=' + encodeURIComponent(state.search) + '&page=' + state.page);
      if (!current() || token !== serial) return;
      results.replaceChildren();
      if (data.scope_status && !['complete', 'ready'].includes(data.scope_status)) append(results, node('p', 'radar-note', '初筛范围：' + label(data.scope_status) + '；请准备候选或查看源结果，未使用全市场底表替代。'));
      if (data.scope_error) append(results, node('p', 'radar-note', data.scope_error));
      if (data.source_snapshots?.length) {
        const sources = node('div', 'stock-scope-sources');
        data.source_snapshots.forEach(source => {
          const row = append(node('div', 'stock-check'), node('strong', '', (source.name || source.index_code) + ' · ' + label(source.status)), node('p', 'radar-meta', '成分快照日期：' + (source.snapshot_date || '未知') + ' · 生效日期：' + (source.effective_date || '未知')), node('p', 'radar-meta', '抓取时间：' + (source.fetched_at || '未知') + ' · ' + (source.complete ? '完整' : '不完整')));
          if (source.url) { try { const url = new URL(source.url); if (url.protocol === 'https:') { const link = node('a', '', '指数原始来源'); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; append(row, link); } } catch (_) { /* 非法链接保留为原始字段，不生成可点击入口。 */ } }
          append(row, fold('原始源摘要', raw(source))); append(sources, row);
        });
        append(results, fold('范围来源、快照日期与抓取时间', sources));
      }
      if (!data.items?.length) append(results, node('p', 'radar-note', state.search ? '未找到匹配的公司。' : '尚无可用初筛范围，请先准备候选。'));
      (data.items || []).forEach(item => {
        const row = node('div', 'stock-row');
        const open = button((item.name || item.symbol) + ' · ' + item.symbol, () => { state.scroll = scrollHost.scrollTop || 0; state.symbol = item.symbol; state.view = 'detail'; show(); }, 'stock-company');
        append(row, open, node('span', 'radar-meta', '行业待核验 · ' + label(item.admission_status)), node('span', 'radar-meta', '资料截点 ' + (item.data_as_of || item.data_asof || '未知')));
        if (item.source_indexes?.length) append(row, node('span', 'radar-meta', '来源指数：' + item.source_indexes.map(v => typeof v === 'string' ? v : v.name || v.code || v.index_id).join('、')));
        append(results, row);
      });
      pager(results, data, page => { state.page = page; show(); }); scrollHost.scrollTop = state.scroll;
    } catch (error) { if (current() && token === serial) errorBox(error, show); }
  }
  function checks(title, value) {
    const section = append(node('section', 'stock-checks'), node('h3', '', title));
    append(section, node('p', 'radar-meta', label(value?.common_status || value?.admission_status || value?.style_status)));
    if (value?.financial_source) {
      const source = value.financial_source;
      append(section, node('p', 'radar-meta stock-financial-source', '盈利来源：结构化财务 · 本次评估截点 ' + (value.as_of || '未知') + ' · 财务研究截点 ' + (source.as_of || '暂无') + ' · 范围日期 ' + (source.scope_as_of || '暂无') + '。' + (source.reason || '资料待补')));
    }
    const details = node('div');
    (value?.checks || []).forEach(check => {
      let extra = '';
      if (check.channel === 'structured_financial') extra = (check.reference_check ? '；本次结构化来源：' : '；结构化来源：') + label(check.structured_check?.outcome) + '，人工事实：' + label(check.manual_check?.outcome);
      if (check.channel === 'supplier_market_value') {
        const reference = check.reference_check || check.supplier_check;
        const values = reference?.values || {};
        extra = '；供应商' + (check.reference_check ? '历史（' + check.reference_as_of + '）' : '本次') + '：' + label(reference?.outcome) + '，人工事实：' + label(check.manual_check?.outcome);
        if (values.amount_yuan != null) extra += '，当前市值 ' + values.amount_yuan + ' 元（' + (values.date || '日期未知') + '）';
        if (values.average_amount_yuan != null) extra += '，60日均值约 ' + values.average_amount_yuan + ' 元（按未舍入值判定）';
      }
      append(details, node('p', 'stock-check', checkLabel(check.name || check.code) + ' · ' + label(check.outcome) + '：' + (check.reason || '待核验') + extra));
    });
    if (value?.checks?.length) append(section, fold('查看核验明细（' + value.checks.length + '项）', details));
    if (value?.unconfigured_rules?.length) append(section, node('p', 'radar-note', '待配置规则：' + value.unconfigured_rules.map(checkLabel).join('、')));
    Object.entries(value?.branches || {}).forEach(([key, branch]) => append(section, fold(key === 'dividend' ? '红利分支' : key === 'value' ? '价值分支' : key, checks('分支条件', branch))));
    return section;
  }
  async function showDetail(token) {
    const symbol = state.symbol;
    const back = button('返回初筛范围', () => { state.detailScroll[symbol] = scrollHost.scrollTop || 0; state.view = 'candidates'; state.symbol = null; show(); }, 'stock-back');
    body.replaceChildren(back, node('p', 'radar-meta', '正在读取公司资料…'));
    try {
      const data = await request(API + '/companies/' + encodeURIComponent(symbol));
      if (!current() || token !== serial) return;
      const company = data.company;
      const draft = state.drafts[symbol] ||= { reason: '', reviewer: '', evidence_id: '', decision: 'needs_evidence', request_id: null };
      body.replaceChildren(back, node('h3', '', company.name + ' · ' + company.symbol), node('p', 'radar-meta', '资料截点 ' + (company.data_as_of || '未知') + ' · 版本 ' + company.revision + ' · 行业待核验'), node('p', 'radar-note', '事实审核不代表资格批准。正式规则尚未启用，新增或恢复成员不可用。'));
      const evaluation = data.evaluation?.items?.[0] || data.evaluation || {};
      append(body, checks('共同条件', evaluation), checks(pool.name + '条件', evaluation.pools?.[pool.id]));
      const evidenceBox = append(node('div', 'stock-evidence'), node('h3', '', '资料与事实审核'));
      const editor = node('div', 'stock-review-editor');
      const reason = node('textarea', 'stock-reason'); reason.placeholder = '填写审核理由或关注原因（必填）'; reason.setAttribute('aria-label', '审核理由'); reason.value = draft.reason;
      const reviewer = node('input', 'stock-reviewer'); reviewer.placeholder = '审核人 / 操作人（必填）'; reviewer.setAttribute('aria-label', '审核人'); reviewer.value = draft.reviewer;
      const editorStatus = node('p', 'radar-meta', draft.reason ? '已保留未保存意见' : '先选择一条资料，再填写审核意见。'); editorStatus.setAttribute('role', 'status');
      const actions = node('div', 'stock-toolbar');
      let saving = false, conflicted = false;
      const decisions = [];
      function selected() { return (data.evidence || []).find(item => item.evidence_id === draft.evidence_id); }
      function sync() { decisions.forEach(b => { b.disabled = saving || conflicted || !selected() || !draft.reason.trim() || !draft.reviewer.trim() || (b.dataset.decision === 'confirmed' && (!selected().confirmable || ['research_hint', 'unresolved'].includes(selected().kind))); }); saveDraft.disabled = saving || conflicted || !draft.reason.trim() || !draft.reviewer.trim(); }
      reason.addEventListener('input', () => { draft.reason = reason.value; draft.request_id = null; editorStatus.textContent = draft.reason ? '未保存意见，已在本会话保留' : ''; sync(); });
      reviewer.addEventListener('input', () => { draft.reviewer = reviewer.value; draft.request_id = null; sync(); });
      async function save(decision) {
        if (saving || conflicted || !current()) return;
        if (!draft.reason.trim() || !draft.reviewer.trim() || (decision && !selected())) return;
        if (decision === 'confirmed' && (!selected().confirmable || ['research_hint', 'unresolved'].includes(selected().kind))) return;
        saving = true; sync();
        const reasonSnapshot = draft.reason, reviewerSnapshot = draft.reviewer, evidenceSnapshot = draft.evidence_id;
        try {
          const bodyData = decision ? { evidence_id: evidenceSnapshot, expected_revision: company.revision, decision, reviewer: reviewerSnapshot.trim(), reason: reasonSnapshot.trim() } : { expected_revision: company.revision, request_id: draft.request_id ||= (globalThis.crypto?.randomUUID?.() || 'web-' + Date.now() + '-' + Math.random().toString(16).slice(2)), actor: reviewerSnapshot.trim(), reason: reasonSnapshot.trim(), pool_id: pool.id };
          const result = await request(API + '/companies/' + encodeURIComponent(symbol) + (decision ? '/reviews' : '/draft'), post(bodyData));
          if (draft.reason === reasonSnapshot && draft.reviewer === reviewerSnapshot && draft.evidence_id === evidenceSnapshot) { draft.reason = ''; draft.request_id = null; }
          if (!current() || token !== serial) return;
          message(decision ? '事实审核已保存；正式成员未改变。' : '已保存关注草案，当前仍受阻，未发布成员。');
          if (!decision) append(notice, fold('草案阻碍与归档结果', raw(result)));
          show();
        } catch (error) {
          if (!current() || token !== serial) return;
          conflicted = error.status === 409;
          editorStatus.textContent = conflicted ? '资料版本已变化，请重新读取后核对再保存。未保存意见已保留。' : '保存失败：' + error.message + '；意见已保留，可重试。';
          if (conflicted) append(editor, button('重新读取资料', show, 'stock-reload'));
        } finally { saving = false; if (current() && token === serial) sync(); }
      }
      [['confirmed', '确认事实'], ['rejected', '否定事实'], ['needs_evidence', '需补充证据']].forEach(([value, text]) => { const b = button(text, () => save(value), 'stock-decision'); b.dataset.decision = value; decisions.push(b); append(actions, b); });
      const saveDraft = button('保存关注草案', () => save(null), 'stock-save-draft');
      const publish = button('新增 / 恢复正式成员', () => {}); publish.disabled = true; publish.title = '正式规则尚未启用，不能发布成员';
      append(editor, editorStatus, reviewer, reason, actions, saveDraft, publish);
      (data.evidence || []).forEach(item => {
        const row = node('div', 'stock-evidence-row');
        const pick = button(label(item.kind) + ' · ' + label(item.review_status), () => { draft.evidence_id = item.evidence_id; editorStatus.textContent = '已选资料：' + label(item.kind) + (item.confirmable && !['research_hint', 'unresolved'].includes(item.kind) ? ' · 可核验原文事实' : ' · 线索不可确认为事实'); sync(); }, 'stock-evidence-select');
        append(row, pick);
        const source = node('div', 'stock-source');
        append(source, node('p', 'radar-meta', '披露日期：' + (item.disclosed_on || '未知')));
        if (typeof item.source_url === 'string') { try { const url = new URL(item.source_url); if (url.protocol === 'https:') { const link = node('a', '', '查看原始来源'); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; append(source, link); } } catch (_) { /* 无效来源不生成链接，原文仍可查看。 */ } }
        append(row, fold('来源与原始字段', append(source, raw(item.payload)))); append(evidenceBox, row);
      });
      if (!data.evidence?.length) append(evidenceBox, node('p', 'radar-note', '尚无证据；需补充可追踪原文后核验。'));
      append(body, evidenceBox, editor, fold('审核历史', raw(data.reviews || [])));
      sync(); scrollHost.scrollTop = state.detailScroll[symbol] || 0;
    } catch (error) { if (current() && token === serial) errorBox(error, show); }
  }
  async function showHistory(token) {
    const drafts = state.view === 'drafts';
    body.replaceChildren(nav(), node('h3', '', drafts ? '关注草案（不代表正式成员）' : '成员变更历史'), node('p', 'radar-meta', '正在读取…'));
    try {
      const data = await request(API + (drafts ? '/drafts?page=' : '/memberships/events?pool_id=' + encodeURIComponent(pool.id) + '&page=') + (state.historyPage || 1));
      if (!current() || token !== serial) return;
      body.replaceChildren(nav(), node('h3', '', drafts ? '关注草案（不代表正式成员）' : '成员变更历史'));
      if (!data.items?.length) append(body, node('p', 'radar-note', '暂无记录。'));
      (data.items || []).forEach(item => append(body, fold((item.symbol || '记录') + ' · ' + label(item.status || item.action), raw(item))));
      pager(body, data, page => { state.historyPage = page; show(); });
    } catch (error) { if (current() && token === serial) errorBox(error, show); }
  }
  function show() { if (!current()) return; const token = ++serial; if (state.view === 'members') showMembers(); else if (state.view === 'candidates') showCandidates(token); else if (state.view === 'detail') showDetail(token); else showHistory(token); }
  show();
  if (taskUI) { busyButtons(false); taskUI.select(pool); } else poll();
  return dispose;
}
