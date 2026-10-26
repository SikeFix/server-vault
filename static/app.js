'use strict';

/* ================= 工具函数 ================= */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const root = $('#root');

const svg = p => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
const ICON = {
  server: svg('<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>'),
  bolt: svg('<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>'),
  bot: svg('<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4M9 14h.01M15 14h.01"/>'),
  list: svg('<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>'),
  gear: svg('<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>'),
  lock: svg('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>'),
};

const STATUS = {
  online: ['在线', 'green'], offline: ['离线', 'red'], auth_failed: ['认证失败', 'orange'],
  error: ['异常', 'orange'], hostkey_mismatch: ['指纹不符', 'red'], unknown: ['未检测', ''],
};

const S = { servers: [], selected: new Set(), q: '', group: '', status: '', detail: null, tab: 'overview', agentInfo: null };

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch('/api' + path, {
    method, headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { }
  if ((res.status === 401 || res.status === 423) && !['/login', '/init', '/state'].includes(path)) {
    boot();
    throw new Error(data?.detail || '会话已失效');
  }
  if (!res.ok) throw new Error(data?.detail || `请求失败 (${res.status})`);
  return data;
}

function toast(msg, type = '') {
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = msg;
  $('#toasts').append(t);
  setTimeout(() => t.remove(), type === 'error' ? 5000 : 2600);
}

const pad = n => String(n).padStart(2, '0');
function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function relTime(iso) {
  if (!iso) return '—';
  const diff = (new Date(iso) - Date.now()) / 1000, a = Math.abs(diff), fut = diff > 0;
  let t;
  if (a < 60) return fut ? '即将' : '刚刚';
  if (a < 3600) t = `${Math.round(a / 60)} 分钟`;
  else if (a < 86400) t = `${Math.round(a / 3600)} 小时`;
  else t = `${Math.round(a / 86400)} 天`;
  return fut ? `${t}后` : `${t}前`;
}
function fmtBytes(n) {
  if (n == null) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)} ${u[i]}`;
}
function fmtUptime(s) {
  if (!s) return '—';
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d} 天 ${h} 小时` : h ? `${h} 小时 ${m} 分` : `${m} 分钟`;
}
function statusBadge(st) {
  const [t, c] = STATUS[st] || [st, ''];
  return `<span class="badge ${c}"><span class="dot ${c}"></span>${t}</span>`;
}
function pctBar(p) {
  if (p == null) return '<span class="muted">—</span>';
  const cls = p >= 90 ? 'crit' : p >= 75 ? 'warn' : '';
  return `<div class="row" style="flex-wrap:nowrap"><div class="bar grow ${cls}"><i style="width:${Math.min(p, 100)}%"></i></div><span class="small">${p}%</span></div>`;
}
function geoText(g) {
  if (!g) return '';
  return [...new Set([g.country, g.region, g.city].filter(Boolean))].join(' ');
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = Object.assign(document.createElement('textarea'), { value: text });
    document.body.append(ta); ta.select(); document.execCommand('copy'); ta.remove();
  }
  toast('已复制到剪贴板', 'ok');
}
async function busy(btn, fn) {
  btn?.classList.add('loading');
  try { return await fn(); }
  catch (e) { toast(e.message, 'error'); }
  finally { btn?.classList.remove('loading'); }
}

function modal({ title, body, foot = '', wide = false }) {
  const mask = document.createElement('div');
  mask.className = 'mask modal-mask';
  const m = document.createElement('div');
  m.className = 'modal' + (wide ? ' wide' : '');
  m.innerHTML = `<div class="modal-head"><span>${esc(title)}</span><button class="x">×</button></div>
    <div class="modal-body">${body}</div>${foot ? `<div class="modal-foot">${foot}</div>` : ''}`;
  document.body.append(mask, m);
  m.close = () => { mask.remove(); m.remove(); };
  mask.onclick = m.close;
  $('.x', m).onclick = m.close;
  return m;
}
function confirmBox(text, { ok = '确定', danger = false } = {}) {
  return new Promise(resolve => {
    const m = modal({ title: '请确认', body: `<div style="white-space:pre-wrap">${esc(text)}</div>`,
      foot: `<button class="btn" data-a="no">取消</button><button class="btn ${danger ? 'danger' : 'primary'}" data-a="yes">${esc(ok)}</button>` });
    const done = v => { m.close(); resolve(v); };
    $('[data-a=no]', m).onclick = () => done(false);
    $('[data-a=yes]', m).onclick = () => done(true);
    $('.x', m).onclick = () => done(false);
  });
}
function codeBox(text) {
  return `<div class="codebox"><pre>${esc(text)}</pre><button class="btn sm copy" data-copy="${esc(text)}">复制</button></div>`;
}
document.addEventListener('click', e => {
  const b = e.target.closest('[data-copy]');
  if (b) { e.stopPropagation(); copy(b.dataset.copy); }
});

/* ================= 启动 / 解锁 ================= */
async function boot() {
  const st = await api('/state');
  if (!st.authed) return renderLock(st);
  if (!$('.app')) renderShell();
  route();
}

function renderLock(st) {
  document.querySelectorAll('.mask,.modal,.drawer').forEach(e => e.remove());
  const init = !st.initialized;
  root.innerHTML = `<div class="lock-page"><form class="lock-card" id="lockForm">
    <div class="brand-logo" style="width:46px;height:46px;color:#fff">${ICON.lock}</div>
    <h2>${init ? '初始化保险库' : '解锁 Server Vault'}</h2>
    <div class="muted small">${init
      ? '首次使用,请设置主密码(至少 8 位)。所有服务器凭据都用主密码派生的 AES-256 密钥加密,主密码无法找回,请妥善保管。'
      : '输入主密码解锁。解锁后 Web 管理端与 Agent 服务即可访问凭据。'}</div>
    <input class="input" type="password" name="pw" placeholder="主密码" autocomplete="current-password" required>
    ${init ? '<input class="input" type="password" name="pw2" placeholder="再次输入主密码" required>' : ''}
    <button class="btn primary">${init ? '创建保险库' : '解锁'}</button>
  </form></div>`;
  const f = $('#lockForm');
  f.pw.focus();
  f.onsubmit = async e => {
    e.preventDefault();
    if (init && f.pw.value !== f.pw2.value) return toast('两次输入的密码不一致', 'error');
    await busy($('button', f), async () => {
      await api(init ? '/init' : '/login', { method: 'POST', body: { password: f.pw.value } });
      root.innerHTML = '';
      await boot();
    });
  };
}

const NAV = [
  { id: 'servers', label: '服务器', icon: 'server' },
  { id: 'automations', label: '自动化', icon: 'bolt' },
  { id: 'agents', label: 'Agent 接入', icon: 'bot' },
  { id: 'audit', label: '审计日志', icon: 'list' },
  { id: 'settings', label: '设置', icon: 'gear' },
];

function renderShell() {
  root.innerHTML = `<div class="app">
    <aside class="side">
      <div class="brand"><div class="brand-logo">${ICON.lock}</div><span>Server Vault</span></div>
      <nav class="nav">${NAV.map(n => `<a href="#${n.id}" data-id="${n.id}" title="${n.label}">${ICON[n.icon]}<span>${n.label}</span></a>`).join('')}</nav>
      <div class="side-foot"><div class="row"><span class="dot green"></span>保险库已解锁</div>
        <button class="btn sm lock-btn" id="lockBtn">锁定保险库</button></div>
    </aside>
    <main class="main" id="main"></main></div>`;
  $('#lockBtn').onclick = async () => {
    if (!await confirmBox('锁定后 Web 会话与所有 Agent 访问都将被拒绝,需要重新输入主密码解锁。', { ok: '锁定' })) return;
    await api('/lock', { method: 'POST' });
    boot();
  };
}

const PAGES = {};
function route() {
  const page = PAGES[location.hash.slice(1)] ? location.hash.slice(1) : 'servers';
  $$('.nav a').forEach(a => a.classList.toggle('active', a.dataset.id === page));
  PAGES[page]().catch(e => toast(e.message, 'error'));
}
window.addEventListener('hashchange', () => $('.app') && route());

/* ================= 服务器列表 ================= */
PAGES.servers = async () => {
  $('#main').innerHTML = `
    <div class="page-head">
      <div><h1>服务器</h1><div class="sub">集中管理凭据、硬件配置、运行状态与网站</div></div>
      <div class="row">
        <button class="btn" id="importBtn">批量导入</button>
        <button class="btn" id="checkAllBtn">全部检测</button>
        <button class="btn primary" id="addBtn">+ 新增服务器</button>
      </div>
    </div>
    <div class="grid c5" id="stats"></div>
    <div class="card" style="margin-top:14px"><div id="toolbar"></div><div class="table-wrap" id="tableWrap"></div></div>`;
  $('#addBtn').onclick = () => serverForm();
  $('#importBtn').onclick = importDialog;
  $('#checkAllBtn').onclick = e => busy(e.currentTarget, async () => {
    const res = await api('/check', { method: 'POST', body: {} });
    toast(`检测完成:${res.filter(r => r.status === 'online').length}/${res.length} 在线`, 'ok');
    await loadServers();
  });
  await loadServers();
};

async function loadServers() {
  S.servers = await api('/servers');
  const ids = new Set(S.servers.map(s => s.id));
  S.selected = new Set([...S.selected].filter(i => ids.has(i)));
  if (!$('#tableWrap')) return;
  renderStats(); renderToolbar(); renderTable();
}

function renderStats() {
  const c = st => S.servers.filter(s => st.includes(s.status)).length;
  const soon = Date.now() + 7 * 86400e3;
  const rot = S.servers.filter(s => s.rotation_status === 'failed' || (s.rotation_enabled && s.next_rotation_at && new Date(s.next_rotation_at) < soon)).length;
  const items = [['服务器总数', S.servers.length, ''], ['在线', c(['online']), 'green'], ['离线', c(['offline']), 'red'],
    ['异常', c(['auth_failed', 'error', 'hostkey_mismatch']), 'orange'], ['7 天内轮换 / 失败', rot, '']];
  $('#stats').innerHTML = items.map(([l, v, cls]) => `<div class="card stat ${cls}"><div class="label">${l}</div><div class="val">${v}</div></div>`).join('');
}

function filtered() {
  const q = S.q.toLowerCase();
  return S.servers.filter(s => (!S.group || s.group_name === S.group) && (!S.status || s.status === S.status) &&
    (!q || [s.name, s.host, s.group_name, s.location, s.provider, s.tags.join(' '), s.facts?.os, s.facts?.model, geoText(s.facts?.geo)]
      .join(' ').toLowerCase().includes(q)));
}

function renderToolbar() {
  const tb = $('#toolbar');
  const n = S.selected.size;
  if (n) {
    tb.className = 'toolbar batchbar';
    tb.innerHTML = `<b>已选 ${n} 台</b><span class="grow"></span>
      <button class="btn sm" data-b="check">检测状态</button>
      <button class="btn sm" data-b="facts">采集配置</button>
      <button class="btn sm" data-b="exec">批量执行</button>
      <button class="btn sm" data-b="rotate">轮换密码</button>
      <button class="btn sm danger" data-b="delete">删除</button>
      <button class="btn sm ghost" data-b="clear">取消选择</button>`;
    $$('[data-b]', tb).forEach(b => b.onclick = () => batchAction(b.dataset.b, b));
    return;
  }
  const groups = [...new Set(S.servers.map(s => s.group_name))];
  tb.className = 'toolbar';
  tb.innerHTML = `<input class="input" id="q" placeholder="搜索名称 / IP / 位置 / 系统 / 型号" style="width:280px" value="${esc(S.q)}">
    <select class="input" id="fGroup"><option value="">全部分组</option>${groups.map(g => `<option ${g === S.group ? 'selected' : ''}>${esc(g)}</option>`).join('')}</select>
    <select class="input" id="fStatus"><option value="">全部状态</option>${Object.entries(STATUS).map(([k, [t]]) => `<option value="${k}" ${k === S.status ? 'selected' : ''}>${t}</option>`).join('')}</select>
    <span class="grow"></span><button class="btn sm ghost" id="reload">刷新</button>`;
  $('#q').oninput = e => { S.q = e.target.value; renderTable(); };
  $('#fGroup').onchange = e => { S.group = e.target.value; renderTable(); };
  $('#fStatus').onchange = e => { S.status = e.target.value; renderTable(); };
  $('#reload').onclick = loadServers;
}

function rotationCell(s) {
  let badge = '';
  if (s.rotation_status === 'failed') badge = '<span class="badge red">轮换失败</span>';
  else if (s.rotation_status === 'warn') badge = '<span class="badge orange">待验证</span>';
  if (!s.rotation_enabled) return badge || '<span class="muted small">未开启</span>';
  return `<div>每 ${s.rotation_days} 天 ${badge}</div><div class="sub">下次 ${relTime(s.next_rotation_at)}</div>`;
}

function renderTable() {
  const rows = filtered();
  const wrap = $('#tableWrap');
  if (!rows.length) {
    wrap.innerHTML = `<div class="empty">${S.servers.length ? '没有匹配的服务器' : '还没有服务器 —— 点击右上角「新增服务器」或「批量导入」开始'}</div>`;
    return;
  }
  const all = rows.every(s => S.selected.has(s.id));
  wrap.innerHTML = `<table class="tbl wide"><thead><tr>
      <th style="width:34px"><input type="checkbox" id="selAll" ${all ? 'checked' : ''}></th>
      <th>状态</th><th>名称</th><th>地址</th><th>位置</th><th>型号 / 系统</th><th>配置</th><th>负载</th><th>密码轮换</th><th></th>
    </tr></thead><tbody>${rows.map(s => {
      const f = s.facts || {}, d = s.status_detail || {};
      const disk = (f.disks || []).reduce((a, x) => a + (x.size || 0), 0);
      return `<tr data-id="${s.id}" class="${S.selected.has(s.id) ? 'sel' : ''}">
        <td data-stop><input type="checkbox" class="sel" ${S.selected.has(s.id) ? 'checked' : ''}></td>
        <td>${statusBadge(s.status)}<div class="sub">${s.latency_ms != null ? s.latency_ms + ' ms' : ''}</div></td>
        <td><div class="name">${esc(s.name)}</div><div class="sub">${esc(s.group_name)}${s.tags.map(t => ` · ${esc(t)}`).join('')}</div></td>
        <td class="mono">${esc(s.username)}@${esc(s.host)}${s.port !== 22 ? ':' + s.port : ''}</td>
        <td>${esc(s.location || geoText(f.geo) || '—')}<div class="sub">${esc(s.provider || f.geo?.isp || '')}</div></td>
        <td>${esc(f.model || f.virt || '—')}<div class="sub">${esc(f.os || '')}</div></td>
        <td>${f.cpu_cores ? f.cpu_cores + ' 核' : '—'} / ${f.mem_total ? fmtBytes(f.mem_total) : '—'}<div class="sub">${disk ? '磁盘 ' + fmtBytes(disk) : ''}</div></td>
        <td>${esc(d.load || '—')}<div class="sub clip" title="${esc(d.error || '')}">${d.mem_pct != null ? `内存 ${d.mem_pct}% · 磁盘 ${d.disk_pct ?? '—'}%` : esc(d.error || '')}</div></td>
        <td>${rotationCell(s)}</td>
        <td data-stop><button class="btn sm" data-term>终端</button></td>
      </tr>`;
    }).join('')}</tbody></table>`;
  $('#selAll').onchange = e => { rows.forEach(s => e.target.checked ? S.selected.add(s.id) : S.selected.delete(s.id)); renderToolbar(); renderTable(); };
  $$('tbody tr', wrap).forEach(tr => {
    const id = +tr.dataset.id;
    tr.onclick = e => { if (!e.target.closest('[data-stop]')) openDetail(id); };
    $('.sel', tr).onchange = e => { e.target.checked ? S.selected.add(id) : S.selected.delete(id); renderToolbar(); renderTable(); };
    $('[data-term]', tr).onclick = () => openTerminal(id);
  });
}

function openTerminal(id) {
  window.open(`/terminal?id=${id}`, `term_${id}`, 'width=1100,height=700');
}

async function batchAction(act, btn) {
  const ids = [...S.selected];
  if (act === 'clear') { S.selected.clear(); renderToolbar(); renderTable(); return; }
  if (act === 'exec') return execDialog(ids);
  if (act === 'check') return busy(btn, async () => {
    const res = await api('/check', { method: 'POST', body: { ids } });
    toast(`检测完成:${res.filter(r => r.status === 'online').length}/${res.length} 在线`, 'ok');
    await loadServers();
  });
  if (act === 'facts') return busy(btn, async () => {
    const res = await api('/facts', { method: 'POST', body: { ids } });
    const bad = res.filter(r => !r.ok);
    toast(`采集完成:成功 ${res.length - bad.length} 台${bad.length ? `,失败 ${bad.length} 台` : ''}`, bad.length ? 'error' : 'ok');
    await loadServers();
  });
  if (act === 'rotate') {
    if (!await confirmBox(`确定立即轮换选中 ${ids.length} 台服务器的登录密码?\n新密码将自动生成并加密保存,旧密码进入历史记录。`, { ok: '立即轮换' })) return;
    return busy(btn, async () => {
      const res = await api('/rotate', { method: 'POST', body: { ids } });
      showResults('密码轮换结果', res.map(r => ({ name: r.server, ok: r.ok && r.verified !== false, text: r.ok ? r.message : r.error })));
      await loadServers();
    });
  }
  if (act === 'delete') {
    if (!await confirmBox(`确定删除选中的 ${ids.length} 台服务器?凭据与历史密码将一并删除,不可恢复。`, { ok: '删除', danger: true })) return;
    return busy(btn, async () => {
      for (const id of ids) await api('/servers/' + id, { method: 'DELETE' });
      S.selected.clear();
      toast('已删除', 'ok');
      await loadServers();
    });
  }
}

function showResults(title, items) {
  modal({ title, wide: true, body: `<div class="list-gap">${items.map(i => `
    <div class="row"><span class="badge ${i.ok ? 'green' : 'red'}">${i.ok ? '成功' : '失败'}</span><b>${esc(i.name)}</b><span class="muted">${esc(i.text || '')}</span></div>`).join('')}</div>` });
}

function execResultHtml(r) {
  if (!r.ok) return `<pre class="out"><span class="err">${esc(r.error)}</span></pre>`;
  return `<pre class="out">${esc(r.stdout)}${r.stderr ? `<span class="err">${esc(r.stderr)}</span>` : ''}${!r.stdout && !r.stderr ? '<span style="color:#8a94ad">(无输出)</span>' : ''}</pre>`;
}

function execDialog(ids) {
  const names = ids.map(id => S.servers.find(s => s.id === id)?.name).filter(Boolean);
  const m = modal({
    title: `批量执行命令 · ${ids.length} 台`, wide: true,
    body: `<div class="muted small" style="margin-bottom:8px">${esc(names.join('、'))}</div>
      <textarea class="input mono" id="bcmd" rows="4" placeholder="例如:df -h / && free -m"></textarea>
      <div class="row" style="margin-top:10px"><label class="check"><input type="checkbox" id="bsudo">使用 sudo(非 root 账号)</label>
      <span class="grow"></span><span class="muted small">超时</span><input class="input" id="btimeout" type="number" value="60" style="width:80px"><span class="muted small">秒</span>
      <button class="btn primary" id="brun">执行</button></div>
      <div id="bres" class="list-gap" style="margin-top:14px"></div>`,
  });
  $('#bcmd', m).focus();
  $('#brun', m).onclick = e => busy(e.currentTarget, async () => {
    const res = await api('/exec', { method: 'POST', body: { ids, command: $('#bcmd', m).value, sudo: $('#bsudo', m).checked, timeout: +$('#btimeout', m).value } });
    $('#bres', m).innerHTML = res.map(r => `<details class="result-item" open><summary>
      <span class="badge ${r.ok && r.exit_code === 0 ? 'green' : 'red'}">${r.ok ? 'exit ' + r.exit_code : '失败'}</span>
      <b>${esc(r.server)}</b><span class="muted small">${r.duration_ms ? r.duration_ms + ' ms' : ''}${r.timed_out ? ' · 超时' : ''}</span></summary>
      ${execResultHtml(r)}</details>`).join('');
  });
}

function importDialog() {
  const m = modal({
    title: '批量导入服务器', wide: true,
    body: `<div class="muted small" style="margin-bottom:8px">CSV 格式,第一行为表头。支持列:name, host, port, username, password, group, location, provider, tags(多个标签用 ; 分隔)</div>
      <textarea class="input mono" id="csv" rows="10">name,host,port,username,password,group,location,provider,tags
web-01,192.168.1.10,22,root,YourPassword,生产,上海机房,阿里云,web;nginx</textarea>
      <div id="impRes" style="margin-top:10px"></div>`,
    foot: '<button class="btn primary" id="doImport">导入</button>',
  });
  $('#doImport', m).onclick = e => busy(e.currentTarget, async () => {
    const r = await api('/servers/import', { method: 'POST', body: { csv: $('#csv', m).value } });
    $('#impRes', m).innerHTML = `<div class="badge green">成功导入 ${r.created.length} 台</div>
      ${r.errors.map(x => `<div class="small" style="color:var(--red);margin-top:4px">${esc(x)}</div>`).join('')}`;
    loadServers();
  });
}

/* ================= 服务器表单 ================= */
async function serverForm(s = null) {
  const v = (k, d = '') => esc(s ? s[k] ?? d : d);
  const groups = [...new Set(S.servers.map(x => x.group_name))];
  const m = modal({
    title: s ? `编辑 · ${s.name}` : '新增服务器', wide: true,
    body: `<form class="form" id="sf" autocomplete="off">
      <div class="field"><label>名称 *</label><input class="input" name="name" value="${v('name')}" required placeholder="web-01"></div>
      <div class="field"><label>分组</label><input class="input" name="group_name" value="${v('group_name', '默认')}" list="glist">
        <datalist id="glist">${groups.map(g => `<option value="${esc(g)}">`).join('')}</datalist></div>
      <div class="field"><label>主机地址 *</label><input class="input" name="host" value="${v('host')}" required placeholder="IP 或域名"></div>
      <div class="field"><label>SSH 端口</label><input class="input" name="port" type="number" value="${v('port', 22)}"></div>
      <div class="field"><label>用户名</label><input class="input" name="username" value="${v('username', 'root')}"></div>
      <div class="field"><label>认证方式</label><select class="input" name="auth_type">
        <option value="password">密码</option><option value="key" ${s?.auth_type === 'key' ? 'selected' : ''}>私钥</option></select></div>
      <div class="field full"><label>登录密码 ${s?.has_password ? '<span class="muted">(已保存,留空则不修改)</span>' : ''}</label>
        <div class="row" style="flex-wrap:nowrap"><input class="input mono" name="password" type="password" autocomplete="new-password" placeholder="${s?.has_password ? '••••••••' : '服务器密码(私钥认证时也用于 sudo / 轮换)'}">
        <button type="button" class="btn" id="genPw">生成</button><button type="button" class="btn" id="showPw">显示</button></div></div>
      <div class="field full keyf"><label>私钥 ${s?.has_key ? '<span class="muted">(已保存,留空则不修改)</span>' : ''}</label>
        <textarea class="input mono" name="private_key" rows="4" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea></div>
      <div class="field keyf"><label>私钥口令</label><input class="input" name="key_passphrase" type="password" placeholder="无则留空"></div>
      <div class="field" style="align-self:end"><label class="check"><input type="checkbox" name="use_sudo" ${s?.use_sudo ? 'checked' : ''}>非 root 账号使用 sudo</label></div>
      <div class="section-title">位置与描述</div>
      <div class="field"><label>位置 / 机房</label><input class="input" name="location" value="${v('location')}" placeholder="如:杭州 · 阿里云华东1(留空则自动识别 IP 归属地)"></div>
      <div class="field"><label>服务商</label><input class="input" name="provider" value="${v('provider')}" placeholder="阿里云 / 腾讯云 / 自建 IDC"></div>
      <div class="field full"><label>标签(逗号分隔)</label><input class="input" name="tags" value="${esc(s ? s.tags.join(', ') : '')}"></div>
      <div class="field full"><label>备注</label><textarea class="input" name="notes" rows="2">${v('notes')}</textarea></div>
      <div class="section-title">密码自动轮换</div>
      <div class="field" style="align-self:end"><label class="check"><input type="checkbox" name="rotation_enabled" ${s?.rotation_enabled ? 'checked' : ''}>开启定期自动轮换</label></div>
      <div class="field"><label>轮换周期(天)</label><input class="input" name="rotation_days" type="number" min="1" value="${v('rotation_days', 30)}"></div>
    </form>`,
    foot: `<button class="btn" id="cancel">取消</button><button class="btn primary" id="save">${s ? '保存' : '添加'}</button>`,
  });
  const f = $('#sf', m);
  const syncKey = () => $$('.keyf', m).forEach(x => x.style.display = f.auth_type.value === 'key' ? '' : 'none');
  f.auth_type.onchange = syncKey; syncKey();
  $('#genPw', m).onclick = async () => { f.password.value = (await api('/generate-password')).password; f.password.type = 'text'; };
  $('#showPw', m).onclick = () => f.password.type = f.password.type === 'password' ? 'text' : 'password';
  $('#cancel', m).onclick = m.close;
  $('#save', m).onclick = e => busy(e.currentTarget, async () => {
    if (!f.reportValidity()) return;
    const data = Object.fromEntries(new FormData(f));
    data.use_sudo = f.use_sudo.checked;
    data.rotation_enabled = f.rotation_enabled.checked;
    data.port = +data.port; data.rotation_days = +data.rotation_days;
    const saved = s ? await api('/servers/' + s.id, { method: 'PUT', body: data }) : await api('/servers', { method: 'POST', body: data });
    m.close();
    toast(s ? '已保存' : '已添加,正在检测连通性…', 'ok');
    if (!s) api('/check', { method: 'POST', body: { ids: [saved.id] } }).then(loadServers).catch(() => { });
    await loadServers();
    if (S.detail === saved.id) renderDetail();
  });
}

/* ================= 服务器详情 ================= */
function closeDrawer() { $$('.drawer, .drawer-mask').forEach(e => e.remove()); S.detail = null; }

async function openDetail(id, tab = 'overview') {
  closeDrawer();
  const mask = document.createElement('div');
  mask.className = 'mask drawer-mask';
  mask.onclick = closeDrawer;
  const d = document.createElement('div');
  d.className = 'drawer';
  d.innerHTML = '<div class="empty">加载中…</div>';
  document.body.append(mask, d);
  S.detail = id; S.tab = tab;
  await renderDetail();
}
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('.modal')) closeDrawer(); });

async function renderDetail() {
  const d = $('.drawer');
  if (!d) return;
  let s;
  try { s = await api('/servers/' + S.detail); } catch (e) { toast(e.message, 'error'); return closeDrawer(); }
  const i = S.servers.findIndex(x => x.id === s.id);
  if (i >= 0) S.servers[i] = s;
  const TABS = [['overview', '概览'], ['sites', '网站与端口'], ['creds', '凭据与轮换'], ['exec', '执行命令'], ['logs', '操作日志']];
  d.innerHTML = `<div class="drawer-head">
      <div class="row" style="align-items:flex-start">
        <div class="grow"><div class="row"><h2 style="margin:0;font-size:19px">${esc(s.name)}</h2>${statusBadge(s.status)}
          ${s.tags.map(t => `<span class="badge">${esc(t)}</span>`).join('')}</div>
          <div class="muted small mono" style="margin-top:3px">${esc(s.username)}@${esc(s.host)}:${s.port} · ${esc(s.group_name)}</div></div>
        <button class="btn sm primary" id="dTerm">打开终端</button>
        <button class="btn sm" id="dCheck">检测</button>
        <button class="btn sm" id="dEdit">编辑</button>
        <button class="btn sm danger" id="dDel">删除</button>
        <button class="x" id="dClose">×</button>
      </div>
      <div class="tabs">${TABS.map(([k, l]) => `<a data-tab="${k}" class="${k === S.tab ? 'active' : ''}">${l}</a>`).join('')}</div>
    </div><div class="drawer-body" id="dbody"></div>`;
  $('#dClose').onclick = closeDrawer;
  $('#dTerm').onclick = () => openTerminal(s.id);
  $('#dEdit').onclick = () => serverForm(s);
  $('#dCheck').onclick = e => busy(e.currentTarget, async () => {
    const [r] = await api('/check', { method: 'POST', body: { ids: [s.id] } });
    toast(r.status === 'online' ? `在线 · ${r.latency_ms} ms` : `${STATUS[r.status]?.[0] || r.status}:${r.detail.error || ''}`, r.status === 'online' ? 'ok' : 'error');
    await renderDetail(); loadServers();
  });
  $('#dDel').onclick = async () => {
    if (!await confirmBox(`确定删除「${s.name}」?凭据与历史密码将一并删除,不可恢复。`, { ok: '删除', danger: true })) return;
    await api('/servers/' + s.id, { method: 'DELETE' });
    closeDrawer(); toast('已删除', 'ok'); loadServers();
  };
  $$('.tabs a', d).forEach(a => a.onclick = () => { S.tab = a.dataset.tab; $$('.tabs a', d).forEach(x => x.classList.toggle('active', x === a)); renderTab(s); });
  renderTab(s);
}

function renderTab(s) {
  const body = $('#dbody');
  ({ overview: tabOverview, sites: tabSites, creds: tabCreds, exec: tabExec, logs: tabLogs })[S.tab](s, body);
}

const kv = pairs => `<dl class="kv">${pairs.filter(p => p).map(([k, v]) => `<dt>${k}</dt><dd>${v === '' || v == null ? '<span class="muted">—</span>' : v}</dd>`).join('')}</dl>`;

function tabOverview(s, body) {
  const f = s.facts || {}, d = s.status_detail || {}, g = f.geo || {};
  const hasFacts = !!s.facts;
  body.innerHTML = `
    ${hasFacts ? '' : `<div class="card card-pad" style="margin-bottom:14px;background:var(--primary-soft);border-color:#d9e0ff">
      <div class="row"><div class="grow"><b>尚未采集配置信息</b><div class="small muted">通过 SSH 读取 CPU、内存、磁盘、厂商型号、系统版本与 IP 归属地</div></div>
      <button class="btn primary" data-facts>立即采集</button></div></div>`}
    <div class="grid c2">
      <div class="card card-pad"><h3>实时状态 <span class="muted small">${s.last_checked_at ? '检测于 ' + relTime(s.last_checked_at) : ''}</span></h3>
        ${kv([['状态', statusBadge(s.status) + (d.error ? `<div class="small" style="color:var(--red);margin-top:4px">${esc(d.error)}</div>` : '')],
          ['延迟', s.latency_ms != null ? s.latency_ms + ' ms' : ''], ['负载', esc(d.load || '')],
          ['内存使用', d.mem_pct != null ? pctBar(d.mem_pct) : ''], ['根分区', d.disk_pct != null ? pctBar(d.disk_pct) : ''],
          ['运行时长', d.uptime_s ? fmtUptime(d.uptime_s) : '']])}</div>
      <div class="card card-pad"><h3>位置信息</h3>
        ${kv([['位置 / 机房', esc(s.location)], ['服务商', esc(s.provider)], ['IP 归属地', esc(geoText(g))],
          ['公网 IP', esc(g.ip || '')], ['运营商', esc(g.isp || '')], ['组织 / AS', esc([g.org, g.as].filter(Boolean).join(' · '))]])}</div>
      <div class="card card-pad"><h3>硬件配置</h3>
        ${kv([['服务器型号', esc(f.model || '')], ['厂商', esc(f.vendor || '')], ['产品名称', esc(f.product || '')], ['序列号', esc(f.serial || '')],
          ['主板', esc(f.board || '')], ['虚拟化', esc(f.virt && f.virt !== 'none' ? f.virt : f.virt === 'none' ? '物理机' : '')],
          ['CPU', esc(f.cpu_model || '')], ['核心数', f.cpu_cores ? `${f.cpu_cores} 核${f.cpu_sockets > 1 ? ` · ${f.cpu_sockets} 路` : ''}` : ''],
          ['内存', f.mem_total ? fmtBytes(f.mem_total) + (f.mem_available ? ` <span class="muted">(可用 ${fmtBytes(f.mem_available)})</span>` : '') : ''],
          ['Swap', f.swap_total != null ? fmtBytes(f.swap_total) : '']])}</div>
      <div class="card card-pad"><h3>系统信息</h3>
        ${kv([['主机名', esc(f.hostname || '')], ['操作系统', esc(f.os || '')], ['内核', esc(f.kernel || '')], ['架构', esc(f.arch || '')],
          ['时区', esc(f.timezone || '')], ['进程数', f.processes ?? ''], ['Docker', esc(f.docker || '')],
          ['内网 IP', esc((f.ips || []).join('  '))]])}</div>
      <div class="card card-pad" style="grid-column:1/-1"><h3>磁盘</h3>
        ${(f.disks || []).length ? `<table class="tbl"><thead><tr><th>挂载点</th><th>容量</th><th>已用</th><th style="width:40%">使用率</th></tr></thead><tbody>
          ${f.disks.map(x => `<tr style="cursor:default"><td class="mono">${esc(x.mount)}</td><td>${fmtBytes(x.size)}</td><td>${fmtBytes(x.used)}</td><td>${pctBar(x.pct)}</td></tr>`).join('')}
          </tbody></table>` : '<div class="muted">—</div>'}</div>
      <div class="card card-pad" style="grid-column:1/-1"><h3>其他</h3>
        ${kv([['备注', esc(s.notes).replace(/\n/g, '<br>')], ['主机指纹', s.host_key_fp ? `<span class="mono">${esc(s.host_key_fp)}</span> <button class="btn sm ghost" id="resetFp">重置</button>` : '<span class="muted">首次连接时记录(TOFU),之后每次连接都会校验</span>'],
          ['创建时间', fmtTime(s.created_at)], ['更新时间', fmtTime(s.updated_at)]])}</div>
    </div>
    <div class="row" style="margin-top:14px"><span class="muted small">配置采集于 ${s.facts_at ? fmtTime(s.facts_at) : '—'}</span><span class="grow"></span>
      <button class="btn" data-facts>刷新配置信息</button></div>`;
  $$('[data-facts]', body).forEach(b => b.onclick = () => busy(b, async () => {
    await api(`/servers/${s.id}/facts`, { method: 'POST' });
    toast('配置信息已更新', 'ok');
    await renderDetail(); loadServers();
  }));
  const rf = $('#resetFp', body);
  if (rf) rf.onclick = async () => {
    if (!await confirmBox('仅在确认服务器重装或更换了主机密钥时才重置指纹。下次连接将重新记录。', { ok: '重置' })) return;
    await api(`/servers/${s.id}/reset-hostkey`, { method: 'POST' });
    renderDetail();
  };
}

function certBadge(exp) {
  if (!exp) return '<span class="badge green">HTTPS</span>';
  const days = Math.floor((new Date(exp) - Date.now()) / 86400e3);
  const c = days < 0 ? 'red' : days < 15 ? 'red' : days < 30 ? 'orange' : 'green';
  return `<span class="badge ${c}" title="证书到期 ${fmtTime(exp)}">HTTPS · ${days < 0 ? '已过期' : `${days} 天后到期`}</span>`;
}

async function tabSites(s, body) {
  body.innerHTML = '<div class="empty">加载中…</div>';
  const data = await api(`/servers/${s.id}/sites`);
  const head = `<div class="row" style="margin-bottom:14px"><div class="grow muted small">
      ${data ? `扫描于 ${fmtTime(data.collected_at)} · 解析 Nginx (nginx -T)、Apache (-S)、监听端口、Docker 容器` : '尚未扫描'}</div>
      <button class="btn primary" id="scan">${data ? '重新扫描' : '扫描网站'}</button></div>`;
  if (!data) {
    body.innerHTML = head + '<div class="card empty">点击「扫描网站」通过 SSH 读取 Web 服务配置,列出站点域名、根目录、反向代理与证书到期时间</div>';
  } else {
    const nginx = data.nginx || [], apache = data.apache || [];
    const siteCard = x => {
      const domains = x.domains.filter(d => d && d !== '_');
      const link = domains.find(d => !d.includes('*') && !d.startsWith('~') && d !== 'localhost');
      const proto = x.ssl ? 'https' : 'http';
      return `<div class="site"><div class="row" style="flex-wrap:nowrap;align-items:flex-start">
          <div class="domains grow">${domains.length ? domains.map(esc).join('  ') : '<span class="muted">(默认站点)</span>'}</div>
          ${x.type === 'stream' ? '<span class="badge">TCP/UDP</span>' : ''}${x.type === 'apache' ? '<span class="badge blue">Apache</span>' : ''}
          ${x.ssl ? certBadge(x.cert_expires) : x.type === 'http' ? '<span class="badge">HTTP</span>' : ''}
          ${link && x.type !== 'stream' ? `<a class="btn sm" href="${proto}://${esc(link)}" target="_blank" rel="noopener">访问</a>` : ''}</div>
        <div class="meta">
          ${x.listen?.length ? `<div>监听:<span class="mono">${esc(x.listen.join(' | '))}</span></div>` : ''}
          ${x.root ? `<div>根目录:<span class="mono">${esc(x.root)}</span></div>` : ''}
          ${x.proxy?.length ? `<div>反向代理:<span class="mono">${esc(x.proxy.join(', '))}</span>${Object.entries(x.upstreams || {}).map(([k, v]) => ` <span class="muted">(${esc(k)} → ${esc(v.join(', '))})</span>`).join('')}</div>` : ''}
          ${x.redirect ? `<div>跳转:<span class="mono">${esc(x.redirect)}</span></div>` : ''}
          ${x.cert ? `<div>证书:<span class="mono">${esc(x.cert)}</span></div>` : ''}
          <div>配置:<span class="mono">${esc(x.file || '')}</span></div>
        </div></div>`;
    };
    body.innerHTML = head + `
      <div class="card card-pad"><h3>网站 (${nginx.length + apache.length})</h3>
        ${nginx.length + apache.length ? `<div class="grid c2">${[...nginx, ...apache].map(siteCard).join('')}</div>`
          : `<div class="muted">${data.nginx_found ? '未解析到站点' : '未检测到 Nginx / Apache(或当前账号无权限读取配置,可开启 sudo)'}</div>`}</div>
      <div class="card card-pad" style="margin-top:14px"><h3>Docker 容器 (${data.docker.length})</h3>
        ${data.docker.length ? `<table class="tbl"><thead><tr><th>名称</th><th>镜像</th><th>端口</th><th>状态</th></tr></thead><tbody>
          ${data.docker.map(c => `<tr style="cursor:default"><td><b>${esc(c.name)}</b></td><td class="mono">${esc(c.image)}</td><td class="mono small">${esc(c.ports)}</td><td>${esc(c.status)}</td></tr>`).join('')}
        </tbody></table>` : '<div class="muted">无运行中的容器或未安装 Docker</div>'}</div>
      <div class="card card-pad" style="margin-top:14px"><h3>监听端口 (${data.ports.length})</h3>
        ${data.ports.length ? `<table class="tbl"><thead><tr><th>端口</th><th>地址</th><th>进程</th></tr></thead><tbody>
          ${data.ports.map(p => `<tr style="cursor:default"><td><b>${p.port}</b></td><td class="mono">${esc(p.addr)}</td><td class="mono">${esc(p.process || '—')}</td></tr>`).join('')}
        </tbody></table>` : '<div class="muted">—</div>'}</div>`;
  }
  $('#scan', body).onclick = e => busy(e.currentTarget, async () => {
    await api(`/servers/${s.id}/sites`, { method: 'POST' });
    toast('扫描完成', 'ok');
    if (S.tab === 'sites') tabSites(s, body);
  });
}

function tabCreds(s, body) {
  let secret = null;
  const getSecret = async () => secret || (secret = await api(`/servers/${s.id}/secret`));
  const rotMsg = s.rotation_status === 'failed' ? `<span class="badge red">上次轮换失败</span> <span class="small" style="color:var(--red)">${esc(s.rotation_error)}</span>`
    : s.rotation_status === 'warn' ? `<span class="badge orange">需关注</span> <span class="small" style="color:var(--orange)">${esc(s.rotation_error)}</span>`
      : s.rotation_status === 'ok' ? '<span class="badge green">正常</span>' : '<span class="muted">尚未轮换</span>';
  body.innerHTML = `<div class="grid c2">
    <div class="card card-pad"><h3>登录凭据</h3>
      ${s.has_pending_password ? '<div class="badge orange" style="margin-bottom:10px">存在未确认的待生效密码(轮换中断),请在「显示」中查看</div>' : ''}
      ${kv([['用户名', `<span class="mono">${esc(s.username)}</span>`], ['认证方式', s.auth_type === 'key' ? '私钥' : '密码'],
        ['密码', s.has_password ? `<span class="secret" id="pw">••••••••••••</span>
          <div class="row" style="margin-top:6px"><button class="btn sm" id="showPw">显示</button><button class="btn sm" id="copyPw">复制密码</button></div>` : '<span class="muted">未保存</span>'],
        s.has_key ? ['私钥', '<button class="btn sm" id="showKey">查看私钥</button>'] : null,
        ['SSH 命令', `<span class="mono">${esc(s.ssh_command)}</span> <button class="btn sm ghost" data-copy="${esc(s.ssh_command)}">复制</button>`],
        ['连接', '<button class="btn sm primary" id="cTerm">Web 终端</button>']])}
      <div id="keyBox" style="margin-top:10px"></div></div>
    <div class="card card-pad"><h3>密码轮换</h3>
      ${kv([['状态', rotMsg], ['自动轮换', s.rotation_enabled ? `<span class="badge green">已开启</span> 每 ${s.rotation_days} 天` : '<span class="badge">未开启</span>'],
        ['上次轮换', s.last_rotated_at ? `${fmtTime(s.last_rotated_at)} <span class="muted">(${relTime(s.last_rotated_at)})</span>` : ''],
        ['下次轮换', s.next_rotation_at ? `${fmtTime(s.next_rotation_at)} <span class="muted">(${relTime(s.next_rotation_at)})</span>` : '']])}
      <div class="hint" style="margin:10px 0">流程:生成强密码 → 预写入保险库 → SSH 执行 chpasswd${s.username !== 'root' ? '(sudo)' : ''} → 用新密码验证登录 → 旧密码归档</div>
      <div class="row"><button class="btn primary" id="rotNow">立即轮换</button><button class="btn" id="rotCustom">指定新密码</button><button class="btn" id="rotPolicy">轮换策略</button></div></div>
    <div class="card card-pad" style="grid-column:1/-1"><h3>历史密码 <button class="btn sm" id="loadHist">查看历史</button></h3>
      <div id="hist" class="muted small">出于安全考虑,历史密码需点击后加载(会记录审计日志)</div></div>
  </div>`;
  const pwEl = $('#pw', body);
  if (pwEl) {
    $('#showPw', body).onclick = e => busy(e.currentTarget, async () => {
      const sec = await getSecret();
      const shown = pwEl.dataset.shown === '1';
      pwEl.textContent = shown ? '••••••••••••' : sec.password + (sec.pending_password ? `   (待生效: ${sec.pending_password})` : '');
      pwEl.dataset.shown = shown ? '' : '1';
      e.currentTarget.textContent = shown ? '显示' : '隐藏';
    });
    $('#copyPw', body).onclick = e => busy(e.currentTarget, async () => copy((await getSecret()).password));
  }
  const sk = $('#showKey', body);
  if (sk) sk.onclick = e => busy(e.currentTarget, async () => { $('#keyBox', body).innerHTML = codeBox((await getSecret()).private_key || ''); });
  $('#cTerm', body).onclick = () => openTerminal(s.id);
  const doRotate = async (btn, password) => busy(btn, async () => {
    const r = await api(`/servers/${s.id}/rotate`, { method: 'POST', body: password ? { password } : {} });
    toast(r.message, r.verified ? 'ok' : 'error');
    await renderDetail(); loadServers();
  });
  $('#rotNow', body).onclick = async e => {
    const btn = e.currentTarget;
    if (await confirmBox(`立即轮换「${s.name}」的 ${s.username} 密码?`, { ok: '立即轮换' })) doRotate(btn);
  };
  $('#rotCustom', body).onclick = () => {
    const m = modal({ title: '指定新密码轮换', body: `<div class="row" style="flex-wrap:nowrap"><input class="input mono" id="np" placeholder="至少 8 位,不能包含冒号">
      <button class="btn" id="g">生成</button></div><div class="hint">将通过 SSH 把服务器密码修改为该值</div>`,
      foot: '<button class="btn primary" id="ok">轮换</button>' });
    $('#g', m).onclick = async () => $('#np', m).value = (await api('/generate-password')).password;
    $('#ok', m).onclick = e => { const v = $('#np', m).value; if (!v) return; m.close(); doRotate($('#rotCustom', body), v); };
  };
  $('#rotPolicy', body).onclick = () => serverForm(s);
  $('#loadHist', body).onclick = e => busy(e.currentTarget, async () => {
    const h = await api(`/servers/${s.id}/history`);
    $('#hist', body).innerHTML = h.length ? `<table class="tbl"><thead><tr><th>时间</th><th>说明</th><th>密码</th><th></th></tr></thead><tbody>
      ${h.map(x => `<tr style="cursor:default"><td>${fmtTime(x.created_at)}</td><td>${esc(x.reason)}</td>
        <td><span class="secret" data-pw="${esc(x.password)}">••••••••</span></td>
        <td><button class="btn sm ghost" data-reveal>显示</button><button class="btn sm ghost" data-copy="${esc(x.password)}">复制</button></td></tr>`).join('')}
      </tbody></table>` : '暂无历史密码';
    $$('[data-reveal]', body).forEach(b => b.onclick = () => { const sp = $('[data-pw]', b.closest('tr')); sp.textContent = sp.textContent.startsWith('•') ? sp.dataset.pw : '••••••••'; });
  });
}

const QUICK = ['uptime', 'df -h', 'free -h', 'top -bn1 | head -20', 'ss -tlnp', 'systemctl --failed', 'docker ps', 'last -n 10', 'journalctl -p err -n 30 --no-pager'];
function tabExec(s, body) {
  body.innerHTML = `<div class="card card-pad">
    <div class="row" style="margin-bottom:8px">${QUICK.map(q => `<button class="btn sm ghost mono" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div>
    <textarea class="input mono" id="cmd" rows="4" placeholder="输入命令,Ctrl/⌘ + Enter 执行"></textarea>
    <div class="row" style="margin-top:10px">
      ${s.username !== 'root' ? '<label class="check"><input type="checkbox" id="sudo">sudo 执行</label>' : ''}
      <span class="grow"></span><span class="muted small">超时</span><input class="input" id="to" type="number" value="60" style="width:80px"><span class="muted small">秒</span>
      <button class="btn primary" id="run">执行</button></div></div>
    <div id="out" style="margin-top:14px"></div>`;
  const cmd = $('#cmd', body);
  $$('[data-q]', body).forEach(b => b.onclick = () => { cmd.value = b.dataset.q; cmd.focus(); });
  const run = () => busy($('#run', body), async () => {
    if (!cmd.value.trim()) return;
    const r = await api(`/servers/${s.id}/exec`, { method: 'POST', body: { command: cmd.value, sudo: $('#sudo', body)?.checked, timeout: +$('#to', body).value } });
    $('#out', body).innerHTML = `<div class="row" style="margin-bottom:6px"><span class="badge ${r.exit_code === 0 ? 'green' : 'red'}">exit ${r.exit_code}</span>
      <span class="muted small">${r.duration_ms} ms${r.timed_out ? ' · 已超时' : ''}</span></div>${execResultHtml({ ok: true, ...r })}`;
  });
  $('#run', body).onclick = run;
  cmd.onkeydown = e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) run(); };
  cmd.focus();
}

async function tabLogs(s, body) {
  const r = await api(`/audit?server_id=${s.id}&limit=200`);
  body.innerHTML = `<div class="card">${auditTable(r.items, false)}</div>`;
}

const ACTIONS = {
  'exec': '执行命令', 'exec.denied': '命令被拦截', 'rotate': '密码轮换', 'secret.reveal': '查看凭据', 'secret.history': '查看历史密码',
  'server.create': '新增服务器', 'server.update': '修改服务器', 'server.delete': '删除服务器', 'server.reset_hostkey': '重置指纹',
  'facts.collect': '采集配置', 'sites.collect': '扫描网站', 'status.check': '状态检测', 'terminal.open': '打开终端', 'terminal.close': '关闭终端',
  'login': '登录', 'vault.init': '初始化', 'vault.lock': '锁定', 'vault.change_master': '修改主密码', 'token.create': '创建 Token',
  'token.revoke': '吊销 Token', 'automation.run': '运行自动化', 'automation.save': '保存自动化', 'automation.delete': '删除自动化', 'settings.update': '修改设置',
};
function auditTable(items, withServer = true) {
  if (!items.length) return '<div class="empty">暂无记录</div>';
  return `<table class="tbl"><thead><tr><th>时间</th><th>操作者</th><th>操作</th>${withServer ? '<th>服务器</th>' : ''}<th>详情</th></tr></thead><tbody>
    ${items.map(a => `<tr style="cursor:default"><td class="small" style="white-space:nowrap">${fmtTime(a.ts)}</td>
      <td><span class="badge ${a.actor.startsWith('agent:') ? 'blue' : a.actor === 'system' ? '' : 'green'}">${esc(a.actor)}</span></td>
      <td>${a.ok ? '' : '<span class="dot red"></span> '}${esc(ACTIONS[a.action] || a.action)}</td>
      ${withServer ? `<td>${esc(a.server_name || '—')}</td>` : ''}
      <td class="mono small" style="max-width:420px;word-break:break-all">${esc(a.detail || '')}</td></tr>`).join('')}</tbody></table>`;
}

/* ================= 自动化 ================= */
const TEMPLATES = {
  '磁盘巡检(使用率 > 80% 告警)': `df -hP | awk 'NR>1 && $5+0 > 80 {print "磁盘告警:", $6, $5; f=1} END {if(!f) print "磁盘正常"}'`,
  '系统负载与内存': 'uptime; free -h',
  '检查可更新软件包': `if command -v apt >/dev/null; then apt list --upgradable 2>/dev/null | tail -n +2 | wc -l; else yum check-update -q | grep -c . ; fi`,
  '清理 7 天前的 journal 日志': 'journalctl --vacuum-time=7d',
  '检查失败的 systemd 服务': 'systemctl --failed --no-legend || true',
  'SSL 证书到期检查(Nginx)': `for f in $(nginx -T 2>/dev/null | awk '/ssl_certificate /{gsub(";","",$2); print $2}' | sort -u); do echo "$f: $(openssl x509 -enddate -noout -in $f | cut -d= -f2)"; done`,
};

PAGES.automations = async () => {
  const [list] = await Promise.all([api('/automations'), S.servers.length ? null : loadServers()]);
  $('#main').innerHTML = `<div class="page-head"><div><h1>自动化</h1><div class="sub">批量巡检脚本,可手动运行或按周期自动执行。密码轮换、状态检测、配置采集的周期见「设置」</div></div>
    <button class="btn primary" id="addAuto">+ 新建任务</button></div>
    <div class="card">${list.length ? `<table class="tbl"><thead><tr><th>任务</th><th>目标</th><th>周期</th><th>最近运行</th><th></th></tr></thead><tbody>
      ${list.map(a => `<tr data-id="${a.id}" style="cursor:default">
        <td><div class="name">${esc(a.name)} ${a.enabled ? '' : '<span class="badge">已停用</span>'} ${a.use_sudo ? '<span class="badge">sudo</span>' : ''}</div>
          <div class="sub mono" style="max-width:420px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(a.script)}</div></td>
        <td>${a.target_type === 'all' ? '全部服务器' : a.target_type === 'group' ? '分组:' + esc(a.target_value) : `指定 ${a.target_value.split(',').filter(Boolean).length} 台`}</td>
        <td>${a.interval_minutes ? `每 ${a.interval_minutes >= 60 && a.interval_minutes % 60 === 0 ? a.interval_minutes / 60 + ' 小时' : a.interval_minutes + ' 分钟'}` : '仅手动'}</td>
        <td>${a.last_run_at ? `<span class="badge ${a.last_status === 'ok' ? 'green' : a.last_status === 'partial' ? 'orange' : 'red'}">${{ ok: '成功', partial: '部分失败', failed: '失败' }[a.last_status] || a.last_status}</span>
          <div class="sub">${relTime(a.last_run_at)}</div>` : '<span class="muted">—</span>'}</td>
        <td style="white-space:nowrap"><button class="btn sm primary" data-a="run">运行</button> <button class="btn sm" data-a="res" ${a.last_result ? '' : 'disabled'}>结果</button>
          <button class="btn sm" data-a="edit">编辑</button> <button class="btn sm danger" data-a="del">删除</button></td></tr>`).join('')}
      </tbody></table>` : '<div class="empty">暂无任务。新建一个巡检脚本,在多台服务器上批量执行</div>'}</div>`;
  $('#addAuto').onclick = () => autoForm();
  $$('tr[data-id]').forEach(tr => {
    const a = list.find(x => x.id === +tr.dataset.id);
    $$('[data-a]', tr).forEach(b => b.onclick = async () => {
      const act = b.dataset.a;
      if (act === 'edit') return autoForm(a);
      if (act === 'res') return autoResults(a.name, a.last_result);
      if (act === 'del') {
        if (!await confirmBox(`删除任务「${a.name}」?`, { ok: '删除', danger: true })) return;
        await api('/automations/' + a.id, { method: 'DELETE' });
        return PAGES.automations();
      }
      if (act === 'run') busy(b, async () => {
        const r = await api(`/automations/${a.id}/run`, { method: 'POST' });
        autoResults(a.name, r.results);
        PAGES.automations();
      });
    });
  });
};

function autoResults(name, results) {
  modal({
    title: `运行结果 · ${name}`, wide: true,
    body: results.length ? `<div class="list-gap">${results.map(r => `<details class="result-item" ${results.length <= 5 ? 'open' : ''}><summary>
      <span class="badge ${r.ok && r.exit_code === 0 ? 'green' : 'red'}">${r.ok ? 'exit ' + r.exit_code : '失败'}</span><b>${esc(r.server)}</b></summary>
      ${execResultHtml(r)}</details>`).join('')}</div>` : '<div class="empty">没有匹配的目标服务器</div>',
  });
}

function autoForm(a = null) {
  const groups = [...new Set(S.servers.map(s => s.group_name))];
  const vals = new Set((a?.target_value || '').split(',').filter(Boolean));
  const m = modal({
    title: a ? '编辑任务' : '新建自动化任务', wide: true,
    body: `<form class="form" id="af">
      <div class="field full"><label>任务名称 *</label><input class="input" name="name" value="${esc(a?.name || '')}" required></div>
      <div class="field full"><label>脚本 * <select class="input" id="tpl" style="width:auto;height:26px;margin-left:8px;font-size:12px"><option value="">从模板填充…</option>
        ${Object.keys(TEMPLATES).map(k => `<option>${esc(k)}</option>`).join('')}</select></label>
        <textarea class="input mono" name="script" rows="6" required>${esc(a?.script || '')}</textarea></div>
      <div class="field"><label>目标</label><select class="input" name="target_type">
        <option value="all">全部服务器</option><option value="group" ${a?.target_type === 'group' ? 'selected' : ''}>按分组</option>
        <option value="ids" ${a?.target_type === 'ids' ? 'selected' : ''}>指定服务器</option></select></div>
      <div class="field"><label>执行周期(分钟,0 = 仅手动)</label><input class="input" name="interval_minutes" type="number" min="0" value="${a?.interval_minutes ?? 0}"></div>
      <div class="field full" id="tgtGroup"><label>选择分组</label><div class="row">${groups.map(g => `<label class="check"><input type="checkbox" value="${esc(g)}" ${vals.has(g) ? 'checked' : ''}>${esc(g)}</label>`).join('') || '<span class="muted">暂无分组</span>'}</div></div>
      <div class="field full" id="tgtIds"><label>选择服务器</label><div class="row">${S.servers.map(s => `<label class="check"><input type="checkbox" value="${s.id}" ${vals.has(String(s.id)) ? 'checked' : ''}>${esc(s.name)}</label>`).join('') || '<span class="muted">暂无服务器</span>'}</div></div>
      <div class="field"><label class="check"><input type="checkbox" name="use_sudo" ${a?.use_sudo ? 'checked' : ''}>非 root 账号使用 sudo</label></div>
      <div class="field"><label class="check"><input type="checkbox" name="enabled" ${!a || a.enabled ? 'checked' : ''}>启用定时执行</label></div>
    </form>`,
    foot: '<button class="btn primary" id="saveA">保存</button>',
  });
  const f = $('#af', m);
  const sync = () => { $('#tgtGroup', m).style.display = f.target_type.value === 'group' ? '' : 'none'; $('#tgtIds', m).style.display = f.target_type.value === 'ids' ? '' : 'none'; };
  f.target_type.onchange = sync; sync();
  $('#tpl', m).onchange = e => { if (e.target.value) { f.script.value = TEMPLATES[e.target.value]; if (!f.name.value) f.name.value = e.target.value; } };
  $('#saveA', m).onclick = e => busy(e.currentTarget, async () => {
    if (!f.reportValidity()) return;
    const box = f.target_type.value === 'group' ? '#tgtGroup' : '#tgtIds';
    const body = {
      name: f.name.value, script: f.script.value, target_type: f.target_type.value,
      target_value: f.target_type.value === 'all' ? '' : $$(`${box} input:checked`, m).map(i => i.value),
      interval_minutes: +f.interval_minutes.value, use_sudo: f.use_sudo.checked, enabled: f.enabled.checked,
    };
    await api(a ? '/automations/' + a.id : '/automations', { method: a ? 'PUT' : 'POST', body });
    m.close(); toast('已保存', 'ok'); PAGES.automations();
  });
}

/* ================= Agent 接入 ================= */
PAGES.agents = async () => {
  const [{ tokens, scopes }, info] = await Promise.all([api('/tokens'), S.agentInfo ? S.agentInfo : api('/agent-info')]);
  S.agentInfo = info;
  const origin = location.origin;
  $('#main').innerHTML = `<div class="page-head"><div><h1>Agent 接入</h1>
      <div class="sub">为 AI Agent 提供服务器访问能力:MCP 协议 + REST API,按 Token 授权、按分组隔离、全程审计</div></div>
      <button class="btn primary" id="newTok">+ 创建 Token</button></div>
    <div class="grid c3" style="margin-bottom:14px">
      <div class="card card-pad"><div class="muted small">MCP 端点(Streamable HTTP)</div><div class="mono" style="margin-top:4px">${esc(origin)}/mcp <button class="btn sm ghost" data-copy="${esc(origin)}/mcp">复制</button></div></div>
      <div class="card card-pad"><div class="muted small">REST API 前缀</div><div class="mono" style="margin-top:4px">${esc(origin)}/api/agent <a class="btn sm ghost" href="/docs#/agent" target="_blank">文档</a></div></div>
      <div class="card card-pad"><div class="muted small">认证方式</div><div class="mono" style="margin-top:4px">Authorization: Bearer &lt;TOKEN&gt;</div></div>
    </div>
    <div class="card" style="margin-bottom:14px"><div class="toolbar"><b>访问令牌</b></div>
      ${tokens.length ? `<table class="tbl"><thead><tr><th>名称</th><th>前缀</th><th>权限</th><th>分组范围</th><th>最近使用</th><th>过期</th><th></th></tr></thead><tbody>
        ${tokens.map(t => `<tr style="cursor:default;${t.revoked ? 'opacity:.5' : ''}"><td><b>${esc(t.name)}</b> ${t.revoked ? '<span class="badge red">已吊销</span>' : ''}</td>
          <td class="mono">${esc(t.prefix)}…</td><td>${t.scopes.map(sc => `<span class="badge ${sc === 'secret' ? 'red' : sc === 'exec' || sc === 'rotate' ? 'orange' : 'blue'}">${sc}</span>`).join(' ')}</td>
          <td>${t.groups.length ? esc(t.groups.join(', ')) : '<span class="muted">全部</span>'}</td>
          <td>${t.last_used_at ? relTime(t.last_used_at) : '<span class="muted">从未</span>'}</td><td>${t.expires_at ? fmtTime(t.expires_at) : '永不'}</td>
          <td>${t.revoked ? '' : `<button class="btn sm danger" data-revoke="${t.id}">吊销</button>`}</td></tr>`).join('')}</tbody></table>`
        : '<div class="empty">还没有 Token。创建一个,把它配置到 Claude Code / Cursor / 自研 Agent 中</div>'}</div>
    <div class="card card-pad" style="margin-bottom:14px"><h3>接入配置 <span class="muted small" id="tokHint">将 &lt;TOKEN&gt; 替换为你创建的令牌</span></h3>
      <div class="tabs" id="cfgTabs" style="margin:0 0 12px;border-bottom:1px solid var(--line)"></div><div id="cfgBody"></div></div>
    <div class="card card-pad"><h3>可用 MCP 工具</h3>
      <table class="tbl"><thead><tr><th>工具</th><th>所需权限</th><th>说明</th></tr></thead><tbody>
      ${info.tools.map(t => `<tr style="cursor:default"><td class="mono"><b>${t.name}</b></td><td>${t.scope ? `<span class="badge">${t.scope}</span>` : '—'}</td><td>${esc(t.description)}</td></tr>`).join('')}
      </tbody></table>
      <div class="hint" style="margin-top:10px">Agent 执行的命令会经过「设置 → Agent 命令拦截规则」检查(如 rm -rf /、mkfs、shutdown 等),所有调用写入审计日志。</div></div>`;
  renderCfg('<TOKEN>');
  $('#newTok').onclick = () => tokenForm(scopes);
  $$('[data-revoke]').forEach(b => b.onclick = async () => {
    if (!await confirmBox('吊销后使用该 Token 的 Agent 将立即失去访问权限。', { ok: '吊销', danger: true })) return;
    await api('/tokens/' + b.dataset.revoke, { method: 'DELETE' });
    PAGES.agents();
  });
};

function renderCfg(token) {
  const origin = location.origin, bridge = S.agentInfo.bridge_path;
  const cfgs = {
    'Claude Code': `claude mcp add --transport http server-vault ${origin}/mcp \\\n  --header "Authorization: Bearer ${token}"`,
    'Cursor / 通用 HTTP': JSON.stringify({ mcpServers: { 'server-vault': { url: `${origin}/mcp`, headers: { Authorization: `Bearer ${token}` } } } }, null, 2),
    'Claude Desktop (stdio)': JSON.stringify({ mcpServers: { 'server-vault': { command: 'python3', args: [bridge], env: { SV_URL: origin, SV_TOKEN: token } } } }, null, 2),
    'REST API': `# 列出服务器\ncurl -s ${origin}/api/agent/servers -H "Authorization: Bearer ${token}"\n\n# 执行命令\ncurl -s -X POST ${origin}/api/agent/servers/web-01/exec \\\n  -H "Authorization: Bearer ${token}" -H "Content-Type: application/json" \\\n  -d '{"command": "df -h", "sudo": false}'\n\n# 批量检测状态\ncurl -s -X POST ${origin}/api/agent/check -H "Authorization: Bearer ${token}" -H "Content-Type: application/json" -d '{}'\n\n# 查看网站\ncurl -s "${origin}/api/agent/servers/web-01/sites?refresh=true" -H "Authorization: Bearer ${token}"`,
  };
  const keys = Object.keys(cfgs);
  let cur = keys[0];
  const draw = () => {
    $('#cfgTabs').innerHTML = keys.map(k => `<a class="${k === cur ? 'active' : ''}" data-k="${esc(k)}">${esc(k)}</a>`).join('');
    $('#cfgBody').innerHTML = codeBox(cfgs[cur]);
    $$('#cfgTabs a').forEach(a => a.onclick = () => { cur = a.dataset.k; draw(); });
  };
  draw();
  if (token !== '<TOKEN>') $('#tokHint').textContent = '已填入刚创建的令牌,离开本页后将不再显示';
}

function tokenForm(scopes) {
  const groups = [...new Set(S.servers.map(s => s.group_name))];
  const m = modal({
    title: '创建 Agent Token', wide: true,
    body: `<form class="form" id="tf">
      <div class="field"><label>名称 *</label><input class="input" name="name" placeholder="如:claude-code-运维" required></div>
      <div class="field"><label>有效期(天,留空 = 永不过期)</label><input class="input" name="expires_days" type="number" min="1"></div>
      <div class="field full"><label>权限</label><div class="grid c2">${Object.entries(scopes).map(([k, d]) => `
        <label class="scope-item"><input type="checkbox" value="${k}" ${k === 'read' || k === 'exec' ? 'checked' : ''}><div><b class="mono">${k}</b><div class="small muted">${esc(d)}</div></div></label>`).join('')}</div></div>
      <div class="field full"><label>分组范围(不选 = 可访问全部服务器)</label><div class="row" id="tg">${groups.map(g => `<label class="check"><input type="checkbox" value="${esc(g)}">${esc(g)}</label>`).join('') || '<span class="muted">暂无分组</span>'}</div></div>
    </form>`,
    foot: '<button class="btn primary" id="mk">创建</button>',
  });
  $('#mk', m).onclick = e => busy(e.currentTarget, async () => {
    const f = $('#tf', m);
    if (!f.reportValidity()) return;
    const r = await api('/tokens', { method: 'POST', body: {
      name: f.name.value, expires_days: f.expires_days.value ? +f.expires_days.value : null,
      scopes: $$('.scope-item input:checked', m).map(i => i.value), groups: $$('#tg input:checked', m).map(i => i.value) } });
    m.close();
    await PAGES.agents();
    renderCfg(r.token);
    modal({ title: 'Token 已创建', body: `<div style="margin-bottom:8px">请立即复制保存,<b>此令牌只显示这一次</b>:</div>${codeBox(r.token)}
      <div class="hint" style="margin-top:10px">下方「接入配置」已自动填入该令牌。</div>` });
  });
}

/* ================= 审计日志 ================= */
PAGES.audit = async (offset = 0) => {
  const actor = $('#aActor')?.value || '', action = $('#aAction')?.value || '';
  const limit = 50;
  const r = await api(`/audit?limit=${limit}&offset=${offset}&actor=${encodeURIComponent(actor)}&action=${encodeURIComponent(action)}`);
  $('#main').innerHTML = `<div class="page-head"><div><h1>审计日志</h1><div class="sub">记录所有登录、凭据查看、命令执行、密码轮换与 Agent 调用</div></div></div>
    <div class="card"><div class="toolbar">
      <input class="input" id="aActor" placeholder="操作者(admin / agent:xxx / system)" value="${esc(actor)}" style="width:240px">
      <select class="input" id="aAction"><option value="">全部操作</option>${Object.entries(ACTIONS).map(([k, v]) => `<option value="${k}" ${k === action ? 'selected' : ''}>${v}</option>`).join('')}</select>
      <button class="btn sm" id="aGo">筛选</button><span class="grow"></span>
      <span class="muted small">共 ${r.total} 条</span>
      <button class="btn sm" id="aPrev" ${offset ? '' : 'disabled'}>上一页</button><button class="btn sm" id="aNext" ${offset + limit < r.total ? '' : 'disabled'}>下一页</button>
    </div>${auditTable(r.items)}</div>`;
  $('#aGo').onclick = () => PAGES.audit(0);
  $('#aPrev').onclick = () => PAGES.audit(Math.max(0, offset - limit));
  $('#aNext').onclick = () => PAGES.audit(offset + limit);
};

/* ================= 设置 ================= */
PAGES.settings = async () => {
  const st = await api('/settings');
  $('#main').innerHTML = `<div class="page-head"><div><h1>设置</h1><div class="sub">自动化周期、密码策略与 Agent 安全策略</div></div></div>
    <div class="grid c2" style="align-items:start">
      <div class="card card-pad"><h3>自动化与策略</h3><form class="form" id="stf">
        <div class="field"><label>自动检测状态间隔(分钟,0 关闭)</label><input class="input" name="status_interval_min" type="number" min="0" value="${st.status_interval_min}"></div>
        <div class="field"><label>自动采集配置间隔(小时,0 关闭)</label><input class="input" name="facts_interval_hours" type="number" min="0" value="${st.facts_interval_hours}"></div>
        <div class="field"><label>生成密码长度(12-64)</label><input class="input" name="password_length" type="number" value="${st.password_length}"></div>
        <div class="field"><label>默认轮换周期(天)</label><input class="input" name="default_rotation_days" type="number" min="1" value="${st.default_rotation_days}"></div>
        <div class="field"><label>命令默认超时(秒)</label><input class="input" name="exec_timeout" type="number" min="1" value="${st.exec_timeout}"></div>
        <div class="field"><label>Web 会话有效期(小时)</label><input class="input" name="session_hours" type="number" min="1" value="${st.session_hours}"></div>
        <div class="field full"><label class="check"><input type="checkbox" name="geo_lookup" ${st.geo_lookup ? 'checked' : ''}>自动识别公网 IP 归属地(调用 ip-api.com,仅发送服务器 IP)</label></div>
        <div class="field full"><label>Agent 命令拦截规则(正则,每行一条)</label>
          <textarea class="input mono" name="agent_deny_patterns" rows="7">${esc(st.agent_deny_patterns.join('\n'))}</textarea>
          <div class="hint">仅对 Agent 生效;Web 管理端手动执行不受限制</div></div>
        <div class="full"><button class="btn primary" id="saveSt">保存设置</button></div></form></div>
      <div>
        <div class="card card-pad"><h3>修改主密码</h3><form class="form" id="mpf">
          <div class="field full"><label>当前主密码</label><input class="input" type="password" name="old" required></div>
          <div class="field"><label>新主密码</label><input class="input" type="password" name="new" required minlength="8"></div>
          <div class="field"><label>确认新主密码</label><input class="input" type="password" name="new2" required></div>
          <div class="full"><button class="btn" id="saveMp">修改主密码</button><div class="hint">将使用新密钥重新加密全部凭据</div></div></form></div>
        <div class="card card-pad" style="margin-top:14px"><h3>安全说明</h3><div class="small muted" style="line-height:1.8">
          · 凭据以 AES-256-GCM 加密存储,密钥由主密码经 scrypt 派生,仅保存在内存中<br>
          · 服务默认只监听 127.0.0.1;如需局域网访问请放在 HTTPS 反向代理之后<br>
          · SSH 首次连接记录主机指纹,之后指纹变化会拒绝连接,防止中间人窃取密码<br>
          · 密码轮换采用「预写」机制:新密码先加密落库再修改服务器,中断也不会丢失<br>
          · 无人值守可设置环境变量 SV_MASTER_PASSWORD 在启动时自动解锁(会降低安全性)</div></div>
      </div></div>`;
  $('#saveSt').onclick = e => { e.preventDefault(); busy(e.currentTarget, async () => {
    const f = $('#stf');
    const body = Object.fromEntries([...new FormData(f)].map(([k, v]) => [k, k === 'agent_deny_patterns' ? v : +v]));
    body.geo_lookup = f.geo_lookup.checked;
    await api('/settings', { method: 'PUT', body });
    toast('设置已保存', 'ok');
  }); };
  $('#saveMp').onclick = e => { e.preventDefault(); const f = $('#mpf'); if (!f.reportValidity()) return;
    if (f.new.value !== f.new2.value) return toast('两次输入的新密码不一致', 'error');
    busy(e.currentTarget, async () => { await api('/change-master', { method: 'POST', body: { old: f.old.value, new: f.new.value } }); f.reset(); toast('主密码已修改', 'ok'); }); };
};

boot().catch(e => toast(e.message, 'error'));
