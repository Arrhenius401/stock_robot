// 自动采集卡片：计划与系统启动分开，持久任务状态由接口轮询。
import { api } from "./api.js";
import { el } from "./components.js";
import { store } from "./state.js";

const append = (parent, ...children) => children.forEach(child => parent.appendChild(child));
const states = { queued:"已排队", running:"采集中", retry_wait:"等待重试", completed:"已完成", partial:"部分失败", failed:"失败", cancelled:"已取消" };
const phases = { syncing:"补齐行情", preparing:"补齐行情", scoring:"计算评分", publishing:"发布快照", waiting:"等待执行", blocked:"计划不可执行", calendar:"准备交易日历", etf:"检查 ETF 行情", overseas:"获取海外指数行情", finalizing:"整理结果" };
const stamp = value => value ? new Date(value).toLocaleString("zh-CN", {timeZone:"Asia/Shanghai",hour12:false}) : "—";

export function mountCollectorSettings(container) {
  const panel = el("section", "panel settings-section collector-panel");
  const head = el("div", "collector-card-head");
  head.appendChild(el("h2", "settings-section-title", "配置雷达自动采集"));
  const setup = el("button", "btn-sm", "系统登录启动");
  setup.type = "button";
  head.appendChild(setup);
  const row = el("div", "collector-note");
  const schedule = el("span", "collector-autostart-status", "正在读取已保存计划…");
  row.appendChild(schedule);
  const scope = el("p", "collector-note");
  const status = el("div", "collector-service-state");
  status.setAttribute("aria-live", "polite");
  const latest = el("p", "collector-note");
  const next = el("p", "collector-note");
  const actions = el("div", "collector-actions");
  const runNow = el("button", "btn-sm", "立即采集");
  const history = el("button", "btn-sm", "查看记录");
  runNow.type = history.type = "button";
  append(actions,runNow, history);
  const message = el("p", "collector-message");
  message.setAttribute("role", "status");
  message.hidden = true;
  append(panel,head, row, scope, status, latest, next, actions, message);
  container.appendChild(panel);
  let payload = null, timer = null, epoch = 0, running = false, busy = false, modal = null, messageKind = "action", messageUntil = 0;
  const alive = generation => generation === epoch && panel.isConnected === true && store.currentView === "settings";
  const notify = (text, kind = "action") => { messageKind = kind; messageUntil = Date.now() + 6000; message.textContent = text; message.hidden = !text; };
  function render(data) {
    payload = data;
    const config = data.settings || data.schedule;
    if (!config) return;

    schedule.textContent = config.enabled ? `每日 ${String(config.hour).padStart(2,"0")}:${String(config.minute).padStart(2,"0")} 检查海外收盘；ETF 按交易日采集（北京时间）` : "已关闭 · 可手动采集";
    scope.textContent = data.config_error ? "标的池配置不可用" : `全部已启用标的池 · ${(data.items || []).length} 个`;
    const active = (data.runs || []).find(run => run.status === "running");
    status.textContent = `${data.service_online ? "服务在线" : "服务离线"} · ${active ? (phases[active.phase] || "采集中") : (data.config_error || data.schedule_error || data.runtime?.last_error) ? "计划不可执行" : config.enabled ? "等待执行" : "待命，可手动采集"}`;
    status.className = `collector-service-state ${data.service_online ? "online" : "offline"}`;
    const last = (data.runs || []).find(run => ["completed","partial","failed"].includes(run.status));
    latest.textContent = last ? `最近采集：${last.etf?.target_date || last.target_date || '最近交易日'} · ${states[last.status]}` : "最近采集：尚无记录";
    next.textContent = config.enabled ? `下次 ETF 采集：${stamp(data.next_scheduled_at)} · 海外检查：${stamp(data.next_overseas_check_at)}${data.service_online ? "" : "（服务恢复后执行）"}` : "下次计划：—";
    runNow.disabled = busy || Boolean(active);
    runNow.textContent = active ? "采集中…" : "立即采集";
    setup.disabled = !config || busy;
    const stateError = data.config_error || data.schedule_error || data.runtime?.last_error;
    if (stateError) notify(stateError, "state");
    else if (messageKind === "state" || Date.now() >= messageUntil) notify("");
    if (modal?.dataset.kind === "history") renderHistory(modal.querySelector(".collector-dialog-body"));
  }
  async function poll() {
    const generation = epoch;
    try {
      const data = await api.radarCollectorStatus();
      if (alive(generation)) render(data);
    } catch (error) {
      if (alive(generation)) notify(`无法读取采集状态：${error.message}`, "state");
    } finally {
      if (alive(generation) && running) timer = window.setTimeout(poll, 3000);
    }
  }
  function pause() {
    running = false;
    epoch += 1;
    window.clearTimeout(timer);
    timer = null;
    if (modal) { modal.close(); modal.remove(); modal = null; }
  }
  function start() {
    if (running || panel.isConnected !== true) return;
    running = true;
    poll();
  }
  function dialog(title, kind) {
    if (modal) { modal.close(); modal.remove(); }
    const node = el("dialog", "collector-dialog");
    node.dataset.kind = kind;
    const heading = el("div", "collector-dialog-head");
    const name = el("h2", "", title);
    name.id = `collector-dialog-${kind}`;
    node.setAttribute("aria-labelledby", name.id);
    const close = el("button", "btn-sm", "关闭");
    close.type = "button";
    close.setAttribute("aria-label", `关闭${title}`);
    close.onclick = () => node.close();
    append(heading,name, close);
    const body = el("div", "collector-dialog-body");
    append(node,heading, body);
    panel.appendChild(node);
    node.addEventListener("close", () => { node.remove(); if (modal === node) modal = null; });
    node.showModal();
    modal = node;
    return body;
  }
  runNow.addEventListener("click", async () => {
    const generation = epoch;
    busy = true;
    runNow.disabled = true;
    try {
      const result = await api.createRadarCollectorRuns({});
      if (alive(generation)) { notify((result.runs || []).every(run=>run.status==="completed") ? "目标交易日已完成" : "采集任务已排队，可在记录中查看进度"); }
    } catch (error) { if (alive(generation)) notify(error.message); }
    finally { busy=false; const data=await api.radarCollectorStatus().catch(()=>payload); if(alive(generation)) render(data); }
  });
  function renderHistory(body) {
    if (!body) return;
    body.replaceChildren();
    const runs = payload?.runs || [];
    if (!runs.length) { body.appendChild(el("p", "collector-note", "尚无采集记录")); return; }
    for (const run of runs) {
      const item = el("section", "collector-history-row");
      item.appendChild(el("h3", "", `${run.etf?.target_date || run.target_date || '最近交易日'} · ${run.universe_id} · ${states[run.status] || run.status}`));
      item.appendChild(el("p", "collector-note", `${run.source === "manual" ? "手动采集" : run.source === "recovery" ? "恢复后补采" : "定时采集"} · 第 ${run.attempt} 次尝试 · ${phases[run.phase] || states[run.status] || run.phase}`));
      const end = ["queued","running","retry_wait"].includes(run.status) ? Date.now() : new Date(run.updated_at).getTime();
      const seconds = Math.max(0, Math.round((end - new Date(run.created_at).getTime()) / 1000));
      item.appendChild(el("p", "collector-note", `任务总耗时（含排队与重试等待）：${Number.isFinite(seconds) ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : "—"}`));
      if(run.error || run.error_summary) item.appendChild(el("p", "collector-message", run.error || run.error_summary));
      if(run.etf) item.appendChild(el("p", "collector-note", `ETF：${states[run.etf.status] || run.etf.status}${run.etf.error ? ` · ${run.etf.error}` : ""}`));
      for(const entry of run.overseas?.items || []) item.appendChild(el("p", "collector-note", `${entry.symbol} · 海外行情截至 ${entry.as_of_date || "暂无"}${entry.error ? ` · ${entry.error}` : ""}`));
      for (const entry of run.items || []) item.appendChild(el("p", "collector-note", `${entry.symbol} · ${states[entry.status] || entry.status}${entry.error_summary ? `：${entry.error_summary}` : ""}`));
      if (["partial","failed","cancelled"].includes(run.status)) {
        const retry = el("button", "btn-sm", "重试失败项");
        retry.type = "button";
        retry.disabled = false;
        retry.onclick = async () => {
          const generation=epoch;
          retry.disabled=true;
          try { await api.retryRadarCollectorRun(run.id); if(alive(generation)) { notify("失败项已重新排队"); modal?.close(); } }
          catch(error) { if(alive(generation)) { notify(error.message); retry.disabled=false; } }
        };
        item.appendChild(retry);
      }
      body.appendChild(item);
    }
  }
  history.onclick = () => renderHistory(dialog("采集记录", "history"));
  setup.onclick = async () => {
    if(!payload?.settings) return;
    const generation=epoch;

    const body=dialog("系统登录启动", "settings");
    const node=modal;
    const form=el("form", "collector-form");
    form.appendChild(el("p", "collector-note", "登录启动项独立管理并立即生效。自动采集计划请在上方表单修改后统一保存。"));
    const startupBox=el("div", "collector-note", "正在读取系统启动设置…"); form.appendChild(startupBox);
    const errorBox=el("p", "collector-message"); errorBox.setAttribute("role","status"); form.appendChild(errorBox);
    const footer=el("div","collector-dialog-footer");
    const cancel=el("button","btn-sm","取消"); cancel.type="button"; cancel.onclick=()=>node.close();
    const save=el("button","settings-save","保存系统启动设置"); save.type="submit";
    append(footer,cancel,save); form.appendChild(footer); body.appendChild(form);
    let startup=null, startupToggle=null;
    form.onsubmit=async event=>{
      event.preventDefault(); save.disabled=true;
      try {
        if(startupToggle && startupToggle.checked!==startup.enabled) await api.updateRadarCollectorStartup(startupToggle.checked);
        if(alive(generation) && modal===node) { node.close(); notify("采集设置已保存"); const data=await api.radarCollectorStatus(); if(alive(generation)) render(data); }
      } catch(error) { if(alive(generation) && modal===node) errorBox.textContent=error.message; }
      finally { save.disabled=false; }
    };
    try {
      startup=await api.radarCollectorStartup();
      if(!alive(generation) || modal!==node) return;
      startupBox.replaceChildren();
      if(startup.editable && startup.enabled!==null) {
        const row=el("label","collector-autostart-row"); row.appendChild(el("span","","登录 Windows 后启动服务"));
        startupToggle=el("input","collector-switch"); startupToggle.type="checkbox"; startupToggle.checked=startup.enabled;
        startupToggle.setAttribute("role","switch"); row.appendChild(startupToggle); startupBox.appendChild(row);
        startupBox.appendChild(el("p","collector-note","服务独立运行，关闭浏览器后仍可采集。"));
      } else startupBox.appendChild(el("p","",startup.provider==="systemd" ? "系统启动设置由服务器部署命令管理。业务采集计划可在此修改。" : "系统启动状态暂不可读取，可手动启动采集服务。"));
      if(startup.error) startupBox.appendChild(el("p","collector-message",startup.error));
    } catch(error) { if(alive(generation) && modal===node) startupBox.textContent=`无法读取系统启动设置：${error.message}`; }
  };
  start();
  return {start,pause,dispose:()=>{pause();panel.remove();}};
}
