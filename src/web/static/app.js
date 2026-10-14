/* Strategy → latest closed data → next-session orders. */
'use strict';
const $ = id => document.getElementById(id);
let strategyId = null;
let strategyConfig = null;
let dataStatus = null;
let activeJob = null;
let pollTimer = null;
let pageVersion = 0;
let viewingHistory = false;
let lastJobId = null;

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok || payload.error) throw new Error(payload.error || payload.detail || '请求失败，请重试。');
  return payload;
}
const endpoint = path => `/api/strategies/${encodeURIComponent(strategyId)}${path}`;
const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const money = value => value == null ? '—' : Number(value).toLocaleString('zh-CN', {maximumFractionDigits:2});
const labels = {buy_and_hold:'买入并持续持有',pending:'待执行',model_account:'模型账户'};
const display = value => value == null ? '—' : typeof value === 'boolean' ? (value ? '是' : '否') : labels[value] || value;

function error(message = '') {
  $('page-error').textContent = message;
  $('page-error').hidden = !message;
}
function updateButtons() {
  const busy = activeJob?.status === 'running';
  $('strategy-select').disabled = busy;
  $('prepare-button').disabled = busy || !strategyId || !strategyConfig;
  $('generate-button').disabled = busy || !dataStatus?.can_generate;
  $('prepare-button').textContent = busy ? '正在更新并计算…' : dataStatus?.has_incomplete_update ? '继续更新并生成计划' : '更新数据并生成计划';
}
function table(id, columns, rows, emptyText) {
  if (!rows.length) { $(id).innerHTML = `<p class="empty">${escape(emptyText)}</p>`; return; }
  const numeric = new Set(['budget','quantity','shares','current_price','avg_cost','pnl_pct','score','target_notional']);
  $(id).innerHTML = `<table><thead><tr>${columns.map(col => `<th>${escape(col.label)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${columns.map(col => {
    const value = row[col.key];
    return `<td>${escape(numeric.has(col.key) ? money(value) : display(value))}</td>`;
  }).join('')}</tr>`).join('')}</tbody></table>`;
}
function clearPlan() {
  $('plan-badge').textContent = '尚未生成'; $('plan-badge').className = 'badge';
  $('plan-dates').textContent = '更新数据并生成计划后，买入、卖出和模型持仓会显示在这里。';
  $('plan-warnings').hidden = true; $('plan-meta').hidden = true;
  $('positions-date').textContent = '随当前计划展示';
  $('buy-count').textContent = '0'; $('sell-count').textContent = '0';
  for (const [id,text] of [['buy-table','尚未生成买入计划'],['sell-table','尚未生成卖出计划'],['positions-table','尚未生成持仓快照']]) table(id,[],[],text);
}
function renderStatus(status) {
  dataStatus = status;
  $('closed-date').textContent = status.latest_trade_date || '待日历确认';
  $('data-date').textContent = status.t_full || '待更新核验';
  $('strategy-name').textContent = status.display_name;
  $('quality-status').textContent = status.writer_active ? '更新中' : !status.audit_current ? '待核验' : status.quality_status === 'review' ? '有源数据缺口' : status.data_current ? '已更新' : '待更新';
  const hints = [];
  if (status.before_close) hints.push('北京时间尚未收盘，计划使用上一已收盘交易日。');
  else if (status.latest_trade_date && status.latest_trade_date !== status.today) hints.push('今日不是已收盘交易日，使用最近交易日的行情。');
  hints.push(...(status.messages || []));
  if (status.data_current) hints.unshift('行情已覆盖最新收盘日，可重新生成所选策略的计划。');
  $('coverage-note').textContent = hints.join(' ') || '点击更新，会先核验数据，再自动生成计划。';
  $('source-dates').innerHTML = strategyConfig.aux_sources.map(source => `<div class="source"><span>${escape(source.display_name)}</span><strong>${escape(status.per_source_dates[source.id] || '无观测')}</strong></div>`).join('');
  updateButtons();
}
function renderPlan(payload, historical = false) {
  const result = payload.result;
  if (!result) { clearPlan(); return; }
  viewingHistory = historical;
  const current = !historical && payload.current;
  $('plan-badge').textContent = historical ? '历史计划' : current ? '最新收盘计划' : '旧计划 · 请更新';
  $('plan-badge').className = `badge ${current ? 'current' : 'stale'}`;
  $('plan-dates').textContent = `信号日 ${result.asof} 收盘 → 执行日 ${result.exec_date || '待日历确认'}${result.exec_date ? ' 开盘' : ''}`;
  const warnings = [...(result.warnings || [])];
  if (historical) warnings.unshift('正在查看历史记录，以下买卖和持仓仅对应这份计划。');
  else if (!current) warnings.unshift('这份计划尚未核实为最新收盘计划，请更新数据后重新生成。');
  if (!result.exec_date && !warnings.some(item => item.includes('日历'))) warnings.push('下一交易日日期尚未确认。');
  $('plan-warnings').textContent = warnings.join('\n'); $('plan-warnings').hidden = !warnings.length;
  const meta = result.meta || {};
  const fields = strategyConfig.market_status_fields;
  $('plan-meta').innerHTML = fields.map(field => `<span>${escape(field.label)} <strong>${escape(display(meta[field.key]))}</strong></span>`).join('');
  $('plan-meta').hidden = false;
  const buys = result.buy || [], sells = result.reduce || [];
  $('buy-count').textContent = buys.length; $('sell-count').textContent = sells.length;
  table('buy-table',strategyConfig.plan_columns.buy,buys,'无买入计划，保留当前仓位或现金。');
  table('sell-table',strategyConfig.plan_columns.sell,sells,'无卖出计划。');
  // Positions and orders must come from this exact plan, including when browsing history.
  table('positions-table',strategyConfig.positions_columns,result.positions || [],'该计划的模型账户为空仓。');
  $('positions-date').textContent = `截至 ${result.asof} 收盘`;
}
async function refresh(version = pageVersion) {
  const sid = strategyId;
  const responses = await Promise.allSettled([api(endpoint('/status')),api(endpoint('/daily/latest')),api(endpoint('/history'))]);
  if (version !== pageVersion || sid !== strategyId) return;
  if (responses[0].status === 'fulfilled') renderStatus(responses[0].value);
  else error(responses[0].reason.message);
  if (responses[1].status === 'fulfilled' && !viewingHistory) renderPlan(responses[1].value);
  if (responses[2].status === 'fulfilled') renderHistory(responses[2].value.reports || []);
}
function renderHistory(reports) {
  if (!reports.length) { $('history-list').innerHTML = '<p class="empty">暂无历史计划</p>'; return; }
  $('history-list').innerHTML = reports.map(report => `<button class="history-item" data-date="${escape(report.date)}"><span>信号 ${escape(report.asof)} → 执行 ${escape(report.exec_date || '待确认')}</span><small>买入 ${report.n_buy} · 卖出 ${report.n_reduce}</small></button>`).join('');
}
async function switchStrategy(id) {
  strategyId = id; const version = ++pageVersion;
  dataStatus = null; strategyConfig = null; viewingHistory = false; error(); clearPlan();
  if (activeJob?.status !== 'running') { activeJob = null; $('job-panel').hidden = true; }
  $('history-list').innerHTML = '<p class="empty">正在加载</p>';
  $('source-dates').innerHTML = ''; $('strategy-name').textContent = '加载中';
  $('closed-date').textContent = $('data-date').textContent = '—';
  $('quality-status').textContent = '加载中'; $('coverage-note').textContent = '正在核对行情覆盖日期。';
  updateButtons();
  const config = await api(endpoint('/config'));
  if (version !== pageVersion) return;
  strategyConfig = config;
  document.title = `${config.display_name} · 次日交易计划`;
  await refresh(version);
}
function renderJob(job) {
  activeJob = job;
  $('job-panel').hidden = job.status === 'idle';
  if (job.status === 'idle') { updateButtons(); return; }
  const descriptions = {running:'正在更新并计算',done:'计划已准备完成',failed:'本次任务未完成',cancelled:'任务已取消',interrupted:'服务退出，任务已中断，可重新更新'};
  $('job-title').textContent = descriptions[job.status] || '任务状态';
  $('job-stage').textContent = job.error || [job.stage,job.substep_detail].filter(Boolean).join(' · ');
  $('cancel-button').hidden = job.status !== 'running';
  const stages = job.stage_ids || [];
  const labelsById = Object.fromEntries([...(strategyConfig?.update_stages || []),...(strategyConfig?.post_update_stages || []),{id:'daily_plan',label:'生成下一交易日计划'}].map(stage => [stage.id,stage.label]));
  const stageStatus = job.steps_status || {};
  const next = stages.find(id => stageStatus[id] !== 'completed');
  $('job-steps').innerHTML = stages.map(id => `<li class="${stageStatus[id] === 'completed' ? 'completed' : stageStatus[id] === 'failed' ? 'failed' : id === next ? 'active' : ''}">${escape(labelsById[id] || id)}${stageStatus[id] === 'completed' ? ' ✓' : ''}</li>`).join('');
  const progress = $('job-progress');
  if (job.status === 'done') progress.value = 100;
  else if (job.internal_total > 0) progress.value = Math.min(98,100 * ((job.completed_steps || 0) + job.internal_step / job.internal_total) / Math.max(1,job.total_steps));
  else progress.removeAttribute('value');
  $('job-log').textContent = (job.log_tail || job.log || []).join('\n');
  updateButtons();
}
async function pollJob() {
  try {
    const job = await api('/api/job/current');
    if (job.status === 'running' && job.strategy_id && job.strategy_id !== strategyId) {
      if ([...$('strategy-select').options].some(option => option.value === job.strategy_id)) {
        $('strategy-select').value = job.strategy_id;
        await switchStrategy(job.strategy_id);
      } else { activeJob = job; updateButtons(); error('另一个策略任务正在运行，请等待完成。'); schedulePoll(); return; }
    }
    if (job.strategy_id === strategyId || job.status === 'idle') {
      renderJob(job);
      if (job.status !== 'running' && job.status !== 'idle' && job.job_id !== lastJobId) {
        lastJobId = job.job_id;
        viewingHistory = false; await refresh();
      }
    } else if (job.status !== 'running') {
      activeJob = null; $('job-panel').hidden = true; updateButtons();
    }
    if (job.status === 'running') schedulePoll();
  } catch (err) { error(`任务状态读取失败：${err.message}`); schedulePoll(); }
}
function schedulePoll() { clearTimeout(pollTimer); pollTimer = setTimeout(pollJob,2000); }
async function start(prepare) {
  error(); viewingHistory = false;
  $('prepare-button').disabled = $('generate-button').disabled = true;
  $('strategy-select').disabled = true;
  try {
    const response = await api(endpoint(prepare ? '/prepare' : '/daily'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({resume:!!dataStatus?.has_incomplete_update})});
    lastJobId = null;
    activeJob = {status:'running',job_id:response.job_id,strategy_id:strategyId};
    await pollJob();
  } catch (err) { error(err.message); updateButtons(); }
}
async function init() {
  try {
    const {strategies} = await api('/api/strategies');
    const configs = await Promise.all(strategies.map(id => api(`/api/strategies/${encodeURIComponent(id)}/config`)));
    $('strategy-select').innerHTML = configs.map(config => `<option value="${escape(config.strategy_id)}">${escape(config.display_name)}</option>`).join('');
    if (!strategies.length) { error('尚无接入操作台的策略。'); return; }
    const saved = (() => { try { return localStorage.getItem('ashare.strategy'); } catch (_) { return null; } })();
    const id = strategies.includes(saved) ? saved : strategies[0];
    $('strategy-select').value = id; await switchStrategy(id); await pollJob();
    setInterval(() => { if (!activeJob || activeJob.status !== 'running') refresh().catch(err => error(err.message)); },30000);
  } catch (err) { error(err.message); }
}
$('strategy-select').addEventListener('change',event => { try { localStorage.setItem('ashare.strategy',event.target.value); } catch (_) {} switchStrategy(event.target.value).catch(err => error(err.message)); });
$('prepare-button').addEventListener('click',() => start(true));
$('generate-button').addEventListener('click',() => start(false));
$('cancel-button').addEventListener('click',async () => { try { await api('/api/job/cancel',{method:'POST'}); await pollJob(); } catch (err) { error(err.message); } });
$('latest-button').addEventListener('click',() => { viewingHistory = false; refresh().catch(err => error(err.message)); });
$('history-list').addEventListener('click',async event => {
  const button = event.target.closest('[data-date]'); if (!button) return;
  const version = pageVersion;
  try { const payload = await api(endpoint('/daily/'+button.dataset.date)); if (version === pageVersion) { renderPlan(payload,true); $('plan-heading').scrollIntoView({behavior:'smooth'}); } }
  catch (err) { error(err.message); }
});
init();
