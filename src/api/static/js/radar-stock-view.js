// 阶段一显示真实空池；未配置能力不能发起 ETF 更新或伪造成员。
export function renderRadarStockPool(root, header, pool, payload) {
  function node(tag, cls, text) { const el = document.createElement(tag); el.className = cls; el.textContent = text; return el; }
  const content = node('section', 'panel radar-stock-empty', '');
  const head = node('div', 'radar-pool-content-head', '');
  const manage = node('button', 'subs-btn', '候选与成员管理'); manage.type = 'button';
  const reason = payload.unavailable_reason || '选股规则和采集流程尚未配置';
  const notice = node('p', 'radar-note', reason); notice.setAttribute('role', 'status');
  manage.addEventListener('click', () => { notice.textContent = '候选与成员管理暂不可用：' + reason; notice.focus(); });
  notice.tabIndex = -1;
  head.append(node('h2', '', pool.name), manage);
  const prepare = node('button', 'subs-btn', '准备候选'); prepare.type = 'button'; prepare.disabled = true; prepare.title = reason;
  content.append(head, node('h3', '', '尚无正式成员'), node('p', 'radar-meta', '审核发布后，这里将按行业展示你的关注名单。'), notice, prepare);
  root.replaceChildren(header, content);
}
