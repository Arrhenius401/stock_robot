// 池选择仅消费目录，业务数据和任务由对应视图负责。
export function createRadarPoolPicker(pools, currentId, onSelect) {
  function node(tag, cls, text) {
    const el = document.createElement(tag); el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  }
  const root = node('div', 'radar-pool-picker');
  const trigger = node('button', 'radar-pool-trigger'); trigger.type = 'button';
  const current = pools.find(pool => pool.id === currentId);
  const label = type => type === 'stock' ? '股票' : '指数 / ETF';
  const copy = node('span', 'radar-pool-copy');
  copy.append(node('small', '', current ? label(current.asset_type) : '投资池'), node('span', '', current ? current.name : '选择投资池'));
  trigger.append(copy, node('span', '', '⌄'));
  trigger.setAttribute('aria-haspopup', 'dialog'); trigger.setAttribute('aria-expanded', 'false');
  const dialog = node('dialog', 'radar-pool-dialog'); dialog.setAttribute('aria-label', '选择投资池');
  const head = node('div', 'radar-pool-dialog-head');
  const close = node('button', '', '×'); close.type = 'button'; close.setAttribute('aria-label', '关闭池选择');
  head.append(node('strong', '', '选择投资池'), close);
  const search = node('input', 'radar-pool-search'); search.type = 'search';
  search.placeholder = '搜索池名称或用途'; search.setAttribute('aria-label', '搜索投资池');
  const options = node('div', 'radar-pool-options');
  const count = node('p', 'radar-pool-count'); count.setAttribute('role', 'status');
  dialog.append(head, search, options, count);
  let buttons = [];
  function render() {
    options.replaceChildren(); buttons = [];
    const query = search.value.trim().toLowerCase();
    const filtered = pools.filter(pool => (pool.name + ' ' + (pool.description || '') + ' ' + label(pool.asset_type)).toLowerCase().includes(query));
    ['etf', 'stock'].forEach(type => {
      const entries = filtered.filter(pool => pool.asset_type === type);
      if (!entries.length) return;
      const group = node('div', ''); group.setAttribute('role', 'group'); group.setAttribute('aria-label', label(type));
      group.append(node('p', 'radar-pool-group', label(type)));
      entries.forEach(pool => {
        const button = node('button', 'radar-pool-option'); button.type = 'button';
        button.setAttribute('aria-pressed', String(pool.id === currentId));
        const text = node('span', 'radar-pool-copy'); text.append(node('span', '', pool.name), node('small', '', pool.description || ''));
        button.append(text, node('span', '', pool.id === currentId ? '✓' : ''));
        button.addEventListener('click', () => { dialog.close(); onSelect(pool); });
        group.append(button); buttons.push(button);
      });
      options.append(group);
    });
    if (!filtered.length) options.append(node('p', 'radar-note', '没有匹配的池，试试其他关键词'));
    count.textContent = filtered.length + ' / ' + pools.length + ' 个池';
  }
  trigger.addEventListener('click', () => {
    search.value = ''; render();
    const rect = trigger.getBoundingClientRect();
    dialog.style.setProperty('--picker-left', Math.max(12, Math.min(rect.left, window.innerWidth - 372)) + 'px');
    dialog.style.setProperty('--picker-top', Math.min(rect.bottom + 8, Math.max(12, window.innerHeight - 460)) + 'px');
    dialog.showModal(); trigger.setAttribute('aria-expanded', 'true'); search.focus();
  });
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { trigger.setAttribute('aria-expanded', 'false'); if (trigger.isConnected !== false) trigger.focus(); });
  dialog.addEventListener('click', event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } });
  search.addEventListener('input', render);
  dialog.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); dialog.close(); return; }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); if (!buttons.length) return;
      const index = buttons.indexOf(document.activeElement);
      const next = index < 0 ? (event.key === 'ArrowDown' ? 0 : buttons.length - 1) : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].focus(); buttons[next].scrollIntoView({block:'nearest'});
    }
    if (event.key === 'Enter' && document.activeElement === search && buttons.length) { event.preventDefault(); buttons[0].click(); }
    if (event.key === 'Tab') {
      const focusables = [close, search, ...buttons]; const index = focusables.indexOf(document.activeElement);
      if (event.shiftKey && index === 0) { event.preventDefault(); focusables[focusables.length - 1].focus(); }
      else if (!event.shiftKey && index === focusables.length - 1) { event.preventDefault(); close.focus(); }
    }
  });
  root.append(trigger, dialog); return root;
}
