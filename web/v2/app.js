// ThinkSmart voice client page v2: renders what the client streams on /events and sends the buttons back.
// Same contract as v1. Open with ?demo (or ?demo=<scene>) to preview without a client.
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const pad = n => String(n).padStart(2, '0');
const WEEK = '日一二三四五六';
const DEMO = new URLSearchParams(location.search).get('demo');   // null when not a demo
if (DEMO !== null && /[?&]still\b/.test(location.search)) document.documentElement.classList.add('still');

const ui = {
  status: { state: 'idle', connected: false, uploading: false },
  models: { current: null, options: [] },
  thread: { entry: null, messages: [] },
  ptt: false,
  bluetooth: null,
  page: null,
};

const SUGGEST = [
  { title: '天气', detail: '明天天气怎么样？', prompt: '明天本地天气怎么样？', icon: 'cloud-sun', style: 'weather' },
  { title: '音乐', detail: '发现喜欢的音乐', prompt: '推荐几首适合现在听的音乐', icon: 'music-2', style: 'music' },
  { title: '智能家居', detail: '控制家里的设备', prompt: '帮我看看家里有哪些可以控制的智能设备', icon: 'house', style: 'home' },
  { title: '计划', detail: '安排这一周', prompt: '帮我安排这周的计划', icon: 'calendar-days', style: 'plan' },
];

/* ---- clock ---- */
function tick() {
  const now = new Date();
  const hm = pad(now.getHours()) + ':' + pad(now.getMinutes());
  $('time').textContent = hm;
  $('bigTime').textContent = hm;
  $('bigDate').textContent = `${now.getMonth() + 1}月${now.getDate()}日  星期${WEEK[now.getDay()]}`;
  const h = now.getHours();
  $('greet').textContent = h < 5 ? '夜深了' : h < 9 ? '早上好' : h < 12 ? '上午好' : h < 14 ? '中午好' : h < 18 ? '下午好' : h < 23 ? '晚上好' : '夜深了';
}

/* ---- microphone feedback; the portrait's independent blink animation is in avatar.js ---- */
const MIC_FLOOR = -66, MIC_FULL = -30;   // dBFS
const micLevel = rms => Math.min(1, Math.max(0, (20 * Math.log10(rms + 1e-6) - MIC_FLOOR) / (MIC_FULL - MIC_FLOOR)));
const STILL = document.documentElement.classList.contains('still');
const level = { target: 0, value: 0, phase: 0, raf: 0, last: 0, state: '' };
function setVoiceFeedback(state) {
  if (state === level.state) return;
  cancelAnimationFrame(level.raf);
  level.raf = 0;
  level.state = state;
  level.target = level.value = 0;
  $('micRing').style.transform = '';
  if (state === 'listening' && !STILL && !document.hidden) {
    level.last = performance.now() - 34;
    level.raf = requestAnimationFrame(levelLoop);
  }
}
function levelLoop(now) {
  if (level.state !== 'listening' || document.hidden) { level.raf = 0; return; }
  level.raf = requestAnimationFrame(levelLoop);
  if (now - level.last < 33) return;
  const dt = Math.min(.1, (now - level.last) / 1000);
  level.last = now;
  level.value += (level.target - level.value) * (level.target > level.value ? .55 : .2);
  level.phase += dt * 2.4;
  const v = level.value, p = level.phase;
  $('micRing').style.transform = `scale(${(1 + v * .16).toFixed(3)})`;
  const pending = $('pending');
  if (pending) pending.querySelectorAll('.wave b').forEach((b, i) => { b.style.transform = `scaleY(${(.18 + v * (.5 + .5 * Math.sin(p * 3 + i * 1.3))).toFixed(3)})`; });
}
document.addEventListener('visibilitychange', () => {
  const state = level.state;
  level.state = '';
  setVoiceFeedback(state);
});

/* ---- status: title, hint, buttons ---- */
function modelName() {
  const cur = ui.models.current;
  return cur ? cur.label : '模型';
}
const shownState = () => (ui.ptt ? 'listening' : ui.status.state);

function renderStatus() {
  const { state, connected } = ui.status;
  const shown = shownState();
  document.body.dataset.state = shown;
  const text = {
    idle: ['说<em>「小葵」</em>', '点按说话，长按录音'],
    connecting: ['正在连接', '正在连接语音服务'],
    listening: ui.ptt ? ['松开<em>发送</em>', '说完松开手指'] : ['正在聆听', '说完后会自动发送'],
    thinking: ['正在思考', modelName()],
    speaking: ['正在回答', '唤醒或点按打断'],
  }[shown] || ['', ''];
  if (ui.status.media_playing && shown === 'idle') text.splice(0, 2, '正在播放', '');
  if (ui.status.recognising && !ui.ptt) text.splice(0, 2, '正在识别', '你说完了，正在把话转成文字');
  const micOff = ui.status.mic === false;
  document.body.classList.toggle('mic-off', micOff);
  const reports = ui.status.reports || 0;
  const sleeping = !!(ui.status.sleep && ui.status.sleep.active);
  document.body.classList.toggle('has-reports', reports > 0);
  document.body.classList.toggle('sleeping', sleeping);
  $('reportText').textContent = reports > 1 ? `有 ${reports} 条待汇报` : '有一条待汇报';
  if (micOff && shown === 'idle') text.splice(0, 2, '麦克风已关', '拨开机身侧面的开关就能说话');
  else if (reports && shown === 'idle') text[1] = '有待汇报：点右上角收听，或叫一声「小葵」';
  else if (sleeping && shown === 'idle' && !connected) text[1] = '睡眠时段：汇报不会自动念';
  $('title').innerHTML = text[0];
  $('hint').textContent = text[1];
  // recognising, thinking or speaking: the turn can be interrupted (the question is dropped if nothing was said yet)
  $('btnAbort').disabled = !ui.status.media_playing && (!connected || !(state === 'speaking' || state === 'thinking' || (ui.status.recognising && !ui.ptt)));
  $('btnEnd').disabled = !connected;
  $('btnAbort').hidden = $('btnAbort').disabled;
  $('btnEnd').hidden = $('btnEnd').disabled;
  $('composer').dataset.active = String(shown !== 'idle');
  const link = $('link');
  link.classList.toggle('on', connected);
  link.querySelector('span').textContent = connected ? '对话中' : '待机';
  renderSide();
  renderPending();
  setVoiceFeedback(shown);
  renderSettings();
  renderBrightnessSchedule();
  renderBright();
  renderScreenOff();
  renderVol();
}

/* ---- models ---- */
const logoSrc = m => (m && m.logo ? `/static/logos/${m.logo}.svg` : '');

function renderModels() {
  const cur = ui.models.current;
  $('pillLogo').src = logoSrc(cur);
  $('pillLabel').textContent = cur ? cur.label : '选择模型';
  $('pillModel').textContent = cur ? (cur.model || cur.company || '') : '';
  const grid = $('grid');
  grid.textContent = '';
  for (const m of ui.models.options) {
    const on = cur && cur.entry === m.entry;
    const b = document.createElement('button');
    b.className = 'card' + (on ? ' on' : '');
    b.innerHTML = `<img class="tile" src="${esc(logoSrc(m))}" alt="">` + (on ? '<span class="badge">使用中</span>' : '') +
      `<span><b>${esc(m.label)}</b><small>${esc(m.model || '')}</small><small class="co">${esc(m.company || '')}</small></span>`;
    b.addEventListener('click', () => switchModel(m));
    grid.appendChild(b);
  }
  renderHead();
  renderHistory();
}

function openSheet(on) {
  $('sheet').classList.toggle('on', on);
}

// The page switches at once and the hub catches up; if it fails, back to what the client last said.
// While a switch is on its way, the client's events about the old model don't flip the page back.
const server = { models: null, thread: null };
const seenThreads = {};   // entry -> the client's messages for that model, shown at once when switching to it
let switching = null;
let historyRevision = 0;

async function switchModel(m) {
  openSheet(false);
  if (ui.models.current && ui.models.current.entry === m.entry) return;
  if (DEMO !== null) return demo.switchTo(m);
  switching = m.entry;
  ui.models = Object.assign({}, ui.models, { current: m });
  ui.thread = { entry: m.entry, messages: seenThreads[m.entry] || [] };
  renderModels(); renderThread(); renderStatus();
  let error = null;
  try {
    const r = await (await fetch('/api/model', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                 body: JSON.stringify({ kind: m.kind, vendor: m.vendor, entry: m.entry }) })).json();
    if (r.ok === false) error = r.error || '切换失败';
  } catch (e) {
    error = '切换失败：客户端没有响应';
  }
  if (switching !== m.entry) return;   // another switch since
  switching = null;
  if (server.models) ui.models = server.models;
  if (server.thread) ui.thread = server.thread;
  renderModels(); renderThread(); renderStatus();
  toast(error || `已切换到 ${m.label}`, error ? 'error' : '');
}

/* ---- settings: sleep hours ---- */
const sleepEdit = {};   // changes sent but not yet confirmed by a state event
let sleepTimer = null;
function sleepSetting() {
  return Object.assign({ enabled: false, start: '22:00', end: '07:00', active: false }, ui.status.sleep || {}, sleepEdit);
}
function renderSettings() {
  const s = sleepSetting();
  $('sleepSwitch').setAttribute('aria-checked', String(s.enabled));
  $('sleepTimes').classList.toggle('off', !s.enabled);
  $('sleepStart').textContent = s.start;
  $('sleepEnd').textContent = s.end;
  $('sleepNote').textContent = !s.enabled ? '睡眠时段已关闭'
    : s.active ? `现在是睡眠时段，到 ${s.end} 结束` : `每天 ${s.start} 到 ${s.end} 是睡眠时段，现在不在`;
}
function sendSleep(changes, delay) {
  Object.assign(sleepEdit, changes);
  renderSettings();
  clearTimeout(sleepTimer);
  sleepTimer = setTimeout(async () => {
    const body = Object.assign({}, sleepEdit);
    if (DEMO) { ui.status.sleep = Object.assign(sleepSetting(), body); for (const k in body) delete sleepEdit[k]; return renderStatus(); }
    try {
      const r = await (await fetch('/api/sleep', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                   body: JSON.stringify(body) })).json();
      if (r.ok === false) toast(r.error || '没保存上', 'error');
    } catch (e) {
      toast('没保存上：客户端没有响应', 'error');
    }
    for (const k in body) if (sleepEdit[k] === body[k]) delete sleepEdit[k];
    renderSettings();
  }, delay);
}
function stepTime(hhmm, minutes) {
  const [h, m] = hhmm.split(':').map(Number);
  const t = ((h * 60 + m + minutes) % 1440 + 1440) % 1440;
  return pad(Math.floor(t / 60)) + ':' + pad(t % 60);
}
$('sleepSwitch').addEventListener('click', () => sendSleep({ enabled: !sleepSetting().enabled }, 0));
for (const b of document.querySelectorAll('.stepper button')) {
  b.addEventListener('click', () => sendSleep({ [b.dataset.k]: stepTime(sleepSetting()[b.dataset.k], Number(b.dataset.d)) }, 600));
}
/* ---- settings: screen brightness (the client hints the system's screen blanker) ---- */
let brightEdit = null;   // the slider's value while the finger is on it or the change is on its way
let brightTimer = null, brightSent = 0, brightDrag = false;
function brightSetting() {
  const setting = Object.assign({ level: 100, dimmed: false }, ui.status.brightness || {});
  return {...setting, level: brightEdit ?? setting.normal ?? setting.level};
}
function renderBright() {
  const b = brightSetting();
  if (brightEdit === null) $('brightRange').value = b.level;
  $('brightRange').style.setProperty('--fill', b.level + '%');
  $('brightNum').textContent = b.level + '%';
  $('brightNote').textContent = b.dimmed ? `定时亮度生效中，当前 ${ui.status.brightness.level}%` : '';
}
async function sendBright(body) {
  if (DEMO) { ui.status.brightness = { level: body.level, dimmed: false }; return; }
  try {
    const r = await (await fetch('/api/brightness', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                       body: JSON.stringify(body) })).json();
    if (r.ok === false) toast(r.error || '没保存上', 'error');
  } catch (e) {
    toast('没保存上：客户端没有响应', 'error');
  }
}
// The slider follows the finger itself (pointer events, like the mic button) instead of relying on
// the browser's own range dragging under touch. While dragging: at most one change every 150 ms
// (the blanker looks every 200 ms); the last one on release.
function dragBright(e) {
  const r = $('brightRange').getBoundingClientRect(), thumb = 48;
  brightEdit = Math.round(Math.max(0, Math.min(1, (e.clientX - r.left - thumb / 2) / (r.width - thumb))) * 100);
  $('brightRange').value = brightEdit;
  renderBright();
  clearTimeout(brightTimer);
  const send = () => { brightSent = Date.now(); sendBright({ level: brightEdit }); };
  if (Date.now() - brightSent > 150) send();
  else brightTimer = setTimeout(send, 150);
}
$('brightRange').addEventListener('pointerdown', e => {
  e.preventDefault();
  brightDrag = true;
  try { $('brightRange').setPointerCapture(e.pointerId); } catch (err) { /* keeps following without capture */ }
  dragBright(e);
});
$('brightRange').addEventListener('pointermove', e => {
  if (brightDrag) dragBright(e);
});
function dropBright() {
  if (!brightDrag) return;
  brightDrag = false;
  clearTimeout(brightTimer);
  const level = brightEdit;
  sendBright({ level }).then(() => { if (brightEdit === level) brightEdit = null; renderStatus(); });
}
$('brightRange').addEventListener('pointerup', dropBright);
$('brightRange').addEventListener('pointercancel', dropBright);
$('brightRange').addEventListener('change', () => {
  if (!brightDrag) sendBright({level: Number($('brightRange').value)});
});

let brightnessScheduleDirty = false;
function renderBrightnessSchedule() {
  const setting = ui.status.brightness_schedule || {enabled: false, start: '00:00', end: '06:00', level: 30};
  $('brightnessScheduleState').textContent = setting.active ? '生效中' : setting.enabled ? '已启用' : '已关闭';
  if (brightnessScheduleDirty) return;
  $('brightnessScheduleEnabled').checked = setting.enabled;
  $('brightnessScheduleStart').value = setting.start;
  $('brightnessScheduleEnd').value = setting.end;
  $('brightnessScheduleLevel').value = setting.level;
}
$('brightnessScheduleForm').addEventListener('input', () => {
  brightnessScheduleDirty = true;
  $('brightnessScheduleEnd').setCustomValidity('');
  $('brightnessScheduleResult').textContent = '';
});
$('brightnessScheduleForm').addEventListener('submit', async event => {
  event.preventDefault();
  const body = {enabled: $('brightnessScheduleEnabled').checked,
    start: $('brightnessScheduleStart').value, end: $('brightnessScheduleEnd').value,
    level: Number($('brightnessScheduleLevel').value)};
  if (body.start === body.end) {
    $('brightnessScheduleEnd').setCustomValidity('开始和结束时间须不同');
    return $('brightnessScheduleEnd').reportValidity();
  }
  $('brightnessScheduleSave').disabled = true;
  try {
    const result = DEMO !== null ? {ok: true, brightness_schedule: {...body, active: false}}
      : await postJson('/api/brightness-schedule', body);
    if (!result.ok) {
      $('brightnessScheduleResult').textContent = result.error || '保存失败，请重试';
      return;
    }
    ui.status.brightness_schedule = result.brightness_schedule;
    brightnessScheduleDirty = false;
    renderBrightnessSchedule();
    $('brightnessScheduleResult').textContent = '已保存';
  } finally {
    $('brightnessScheduleSave').disabled = false;
  }
});

/* ---- settings: auto screen-off (the client hints the system's screen blanker) ---- */
const offEdit = {};   // a change on its way
function offSetting() {
  return Object.assign({ enabled: true, minutes: 5 }, ui.status.screen_off || {}, offEdit);
}
function renderScreenOff() {
  const s = offSetting();
  $('offSwitch').setAttribute('aria-checked', String(s.enabled));
  $('offMinutes').classList.toggle('off', !s.enabled);
  for (const b of $('offMinutes').children) b.classList.toggle('on', Number(b.dataset.m) === s.minutes);
  $('offNote').textContent = s.enabled ? `${s.minutes} 分钟没碰屏幕、也没在对话，就熄屏` : '屏幕一直亮着，不会自动熄屏';
}
async function sendScreenOff(changes) {
  Object.assign(offEdit, changes);
  renderScreenOff();
  if (DEMO) { ui.status.screen_off = offSetting(); for (const k in changes) delete offEdit[k]; return renderStatus(); }
  try {
    const r = await (await fetch('/api/screen-off', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                      body: JSON.stringify(changes) })).json();
    if (r.ok === false) toast(r.error || '没保存上', 'error');
  } catch (e) {
    toast('没保存上：客户端没有响应', 'error');
  }
  for (const k in changes) if (offEdit[k] === changes[k]) delete offEdit[k];
  renderScreenOff();
}
$('offSwitch').addEventListener('click', () => sendScreenOff({ enabled: !offSetting().enabled }));
for (const b of $('offMinutes').children) b.addEventListener('click', () => sendScreenOff({ minutes: Number(b.dataset.m) }));

/* ---- settings: volume ---- */
// Same finger-following slider as the brightness: at most one change every 150 ms while dragging, the
// last one on release; what the client says meanwhile doesn't move the thumb under the finger.
function slider(id, numId, get, send) {
  const el = $(id);
  let edit = null, timer = null, sent = 0, drag = false;
  function render() {
    const v = edit === null ? get() : edit;
    if (edit === null && v != null) el.value = v;
    el.style.setProperty('--fill', (v == null ? 0 : v) + '%');
    $(numId).textContent = v == null ? '' : v + '%';
  }
  function move(e) {
    const r = el.getBoundingClientRect(), thumb = 48;
    edit = Math.round(Math.max(0, Math.min(1, (e.clientX - r.left - thumb / 2) / (r.width - thumb))) * 100);
    el.value = edit;
    render();
    clearTimeout(timer);
    const go = () => { sent = Date.now(); send(edit); };
    if (Date.now() - sent > 150) go();
    else timer = setTimeout(go, 150);
  }
  el.addEventListener('pointerdown', e => {
    e.preventDefault();
    drag = true;
    try { el.setPointerCapture(e.pointerId); } catch (err) { /* keeps following without capture */ }
    move(e);
  });
  el.addEventListener('pointermove', e => { if (drag) move(e); });
  function drop() {
    if (!drag) return;
    drag = false;
    clearTimeout(timer);
    const v = edit;
    Promise.resolve(send(v)).then(() => { if (edit === v) edit = null; render(); });
  }
  el.addEventListener('pointerup', drop);
  el.addEventListener('pointercancel', drop);
  return render;
}
async function postJson(path, body) {
  try {
    const r = await (await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                         body: JSON.stringify(body || {}) })).json();
    if (r.ok === false) toast(r.error || '没保存上', 'error');
    return r;
  } catch (e) {
    toast('没保存上：客户端没有响应', 'error');
    return { ok: false };
  }
}
const renderVol = slider('volRange', 'volNum', () => ui.status.volume,
  v => (DEMO ? (ui.status.volume = v) : postJson('/api/volume', { volume: v })));
/* ---- settings: chat history (kept on the hub; clearing also clears the hub's copy) ---- */
let clearArmed = 0, memArmed = 0;   // first tap arms a button, a second one within 5 s clears
function renderClear() {
  $('clearBtn').textContent = Date.now() - clearArmed < 5000 ? '确认清除' : '清除';
  $('clearBtn').classList.toggle('armed', Date.now() - clearArmed < 5000);
  $('clearMemBtn').textContent = Date.now() - memArmed < 5000 ? '确认只清除记忆' : '只清除记忆、保留记录';
  $('clearMemBtn').classList.toggle('armed', Date.now() - memArmed < 5000);
}
function agentNote(r) {
  // Claude Code and Grok Bot keep their own context: clearing here doesn't touch it
  return (r.agents || []).length ? `${r.agents.join('和')}自己那边的对话没有动` : '';
}
$('clearBtn').addEventListener('click', async () => {
  if (Date.now() - clearArmed > 5000) {
    clearArmed = Date.now();
    renderClear();
    setTimeout(renderClear, 5100);
    return toast('清除后所有模型的聊天记录都会删掉，再点一下「确认清除」');
  }
  clearArmed = 0;
  renderClear();
  if (DEMO !== null) { handlers.thread({ entry: ui.thread.entry, messages: [] }); return toast('聊天记录已清除'); }
  const r = await (await post('/api/history/clear', {}))?.json?.();
  if (!r || !r.ok) return toast((r && r.error) || '没能清除', 'error');
  const note = agentNote(r);
  toast('聊天记录已清除' + (note ? '。' + note : ''));
});
$('clearMemBtn').addEventListener('click', async () => {
  if (Date.now() - memArmed > 5000) {
    memArmed = Date.now();
    renderClear();
    setTimeout(renderClear, 5100);
    return toast('模型会忘掉以前聊过的，聊天记录留着；再点一下「确认只清除记忆」');
  }
  memArmed = 0;
  renderClear();
  if (DEMO !== null) return toast('记忆已清除，聊天记录还在');
  const r = await (await post('/api/history/clear', { memory_only: true }))?.json?.();
  toast(r && r.ok ? '记忆已清除，聊天记录还在' : (r && r.error) || '没能清除', r && r.ok ? '' : 'error');
});
renderClear();

// The device's address, read each time the sheet opens (it can change with the WiFi)
async function renderIp() {
  let ip = DEMO !== null ? '192.168.1.23' : null;
  if (DEMO === null) {
    try { ip = (await (await fetch('/api/network')).json()).ip; } catch (e) { ip = null; }
  }
  $('ipNum').textContent = ip || '未联网';
  $('ipSub').textContent = ip ? '' : '尚未连接网络';
}
$('btnSettings').addEventListener('click', () => { $('settings').classList.add('on'); renderIp(); renderWifiCard(); loadServer(); });

/* ---- settings: remote Hub ---- */
let serverBusy = false, serverDirty = false;
const serverValues = () => ({ hub_http: $('serverHttp').value.trim(), hub_ws: $('serverWs').value.trim(),
  pairing_token: $('serverPairingToken').value.trim(), clear_pairing: $('serverPairingClear').checked });
function serverMessage(text, kind = '') {
  $('serverResult').textContent = text;
  $('serverResult').dataset.kind = kind;
}
function serverWorking(busy, action) {
  serverBusy = busy;
  $('serverSave').disabled = $('serverCheck').disabled = busy;
  $('serverHttp').disabled = $('serverWs').disabled = $('serverPairingToken').disabled = $('serverPairingClear').disabled = busy;
  $('serverState').textContent = busy ? action : '';
}
async function loadServer() {
  if (serverBusy || serverDirty) return;
  try {
    const r = await fetch('/api/server');
    if (!r.ok) throw new Error();
    const data = await r.json();
    // Do not overwrite text entered while this request was in flight.
    if (serverDirty) return;
    $('serverHttp').value = data.hub_http;
    $('serverWs').value = data.hub_ws;
    $('serverPairingToken').value = '';
    $('serverPairingClear').checked = false;
    $('serverPairingState').textContent = data.pairing_configured ? '已配置' : '未配置';
  } catch (_) { serverMessage('无法读取服务器配置，请重新打开设置', 'error'); }
}
async function checkServer() {
  if (serverBusy) return;
  if (!$('serverForm').reportValidity()) return;
  serverWorking(true, '正在检测');
  serverMessage('');
  try {
    const r = await fetch('/api/server/check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(serverValues()) });
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error(data.error || '检测失败');
    serverMessage(`${data.voice.message}；${data.http.message}`, data.connected ? 'ok' : 'warning');
  } catch (e) { serverMessage(e.message || '客户端没有响应', 'error'); }
  finally { serverWorking(false); }
}
for (const id of ['serverHttp', 'serverWs', 'serverPairingToken']) $(id).addEventListener('input', () => {
  serverDirty = true;
  if (id === 'serverHttp') $('serverWs').value = ''; // new HTTP host must never keep the old voice host
  if (id === 'serverPairingToken') $('serverPairingClear').checked = false;
  serverMessage('');
});
$('serverPairingClear').addEventListener('change', () => {
  serverDirty = true;
  if ($('serverPairingClear').checked) $('serverPairingToken').value = '';
  serverMessage('');
});
$('serverCheck').addEventListener('click', checkServer);
$('serverForm').addEventListener('submit', async e => {
  e.preventDefault();
  if (serverBusy || !$('serverForm').reportValidity()) return;
  serverWorking(true, '正在保存');
  try {
    const r = await fetch('/api/server', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(serverValues()) });
    const data = await r.json();
    if (!r.ok || !data.ok) throw new Error(data.error || '保存失败');
    $('serverHttp').value = data.hub_http;
    $('serverWs').value = data.hub_ws;
    $('serverPairingToken').value = '';
    $('serverPairingClear').checked = false;
    $('serverPairingState').textContent = data.pairing_configured ? '已配置' : '未配置';
    serverDirty = false;
    toast('连接设置已保存');
    serverWorking(false);
    await checkServer();
  } catch (e) { serverMessage(e.message || '客户端没有响应', 'error'); }
  finally { serverWorking(false); }
});
/* ---- settings: WiFi (the kiosk shell scans and joins: thinksmart-kiosk.py, the same code as its welcome page) ---- */
// The shell answers the client's page only on the device's own screen (127.0.0.1); window.thinksmartShell
// says it can. An older shell can't: the card then only says so.
const shell = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.thinksmart;
const shellWifi = !!(shell && window.thinksmartShell && window.thinksmartShell.wifi);
const shellCalls = {};
let shellSeq = 0;
// The shell answers every command (a connect takes at most ~60 s: three short nmcli calls, a
// watched activation of at most 50 s, the cleanup); an older shell (the 20261101 image) doesn't when
// nmcli hangs. Waiting for ever would keep the Wi-Fi panel busy: after this, it counts as failed.
const SHELL_TIMEOUT = 90000;
function shellCall(cmd, args) {
  return new Promise(resolve => {
    const id = ++shellSeq;
    shellCalls[id] = resolve;
    setTimeout(() => window.thinksmartReply({ id, ok: false, reason: 'noreply' }), SHELL_TIMEOUT);
    const msg = Object.assign({ id, cmd }, args || {});
    if (shellWifi) shell.postMessage(JSON.stringify(msg));
    else wifiMock(msg);
  });
}
window.thinksmartReply = function (r) {
  const done = shellCalls[r.id];
  delete shellCalls[r.id];
  if (done) done(r);
};
// outside the kiosk (a desktop browser, ?demo): pretend to be a device; password "wrongpass" fails
function wifiMock(m) {
  const reply = (r, ms) => setTimeout(() => window.thinksmartReply(Object.assign({ id: m.id }, r)), ms);
  if (DEMO === null) return reply({ ok: false, reason: 'noshell' }, 0);
  if (m.cmd === 'wifi_status') reply({ ok: true, ssid: 'Home_5G', signal: 82 }, 100);
  else if (m.cmd === 'scan') reply({ ok: true, networks: [
    { ssid: 'Home_5G', signal: 82, secure: true, active: true, saved: true },
    { ssid: 'Home', signal: 70, secure: true, saved: true },
    { ssid: '客厅的 WiFi', signal: 54, secure: true },
    { ssid: 'Guest', signal: 40, secure: false },
    { ssid: 'TP-LINK_8A2C', signal: 22, secure: true },
  ] }, 700);
  else if (m.cmd === 'connect') {
    ['scan', 'join', 'auth', 'ip'].forEach((s, i) => setTimeout(() => window.thinksmartProgress({ id: m.id, stage: s }), 300 + i * 400));
    reply(m.password === 'wrongpass' ? { ok: false, reason: 'password' } : { ok: true }, 2000);
  } else reply({ ok: true }, 200);
}

const WIFI_REASONS = {
  password: '密码不对，请再试一次',
  notfound: '找不到这个网络，可能离得太远或名称有误',
  timeout: '连接超时，请靠近路由器后再试',
  noreply: '设备没有回应，请过一会儿再看看网络状态',
};
const WIFI_STAGES = { scan: '正在搜索网络…', join: '正在连接路由器…', auth: '正在验证密码…', ip: '密码正确，正在获取网络地址…' };
const wifi = { nets: [], target: null, ssid: '', pw: '', showPw: false, busy: false, connectId: null };
const signalText = s => s > 75 ? '信号很好' : s > 50 ? '信号良好' : s > 25 ? '信号一般' : '信号较弱';

async function renderWifiCard() {
  if (!shellWifi && DEMO === null) {
    $('wifiSub').textContent = '当前显示环境不支持切换 WiFi';
    $('wifiBtn').hidden = true;
    return;
  }
  const r = await shellCall('wifi_status');
  $('wifiSub').textContent = !r.ok ? '读不到 WiFi 状态' : r.ssid ? `已连接 ${r.ssid} ， ${signalText(r.signal)}` : '没有连接 WiFi';
}
function openWifi() {
  $('wifi').classList.add('on');
  wifiList();
  wifiScan();
}
function closeWifi() {
  if (wifi.busy) return;   // a connection attempt is on: it rolls back by itself if it fails
  if (document.activeElement) document.activeElement.blur();
  $('wifi').classList.remove('on');
  renderWifiCard();
  renderIp();
}
function wifiPane(id) {
  for (const p of document.querySelectorAll('.w-pane')) p.classList.toggle('on', p.id === id);
}
function wifiList() {
  if (document.activeElement) document.activeElement.blur();
  wifiPane('wList');
  renderNets();
}
async function wifiScan() {
  $('wScan').classList.add('spin');
  if (!wifi.nets.length) $('wNets').innerHTML = '<div class="w-empty">正在搜索附近的 WiFi…</div>';
  const r = await shellCall('scan');
  $('wScan').classList.remove('spin');
  wifi.nets = r.ok ? r.networks : [];
  renderNets(r.ok ? '' : '搜索失败，点右上角刷新再试');
}
function bars(signal) {
  const lit = signal > 75 ? 4 : signal > 50 ? 3 : signal > 25 ? 2 : 1;
  return '<span class="bars">' + [1, 2, 3, 4].map(i => `<i class="${i <= lit ? 'lit' : ''}"></i>`).join('') + '</span>';
}
const LOCK = '<svg class="w-lock" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';
function renderNets(error) {
  const box = $('wNets');
  if (error || !wifi.nets.length) {
    box.innerHTML = `<div class="w-empty">${esc(error || '附近没有搜到 WiFi')}</div>`;
    return;
  }
  box.innerHTML = wifi.nets.map((n, i) =>
    `<button class="w-net${n.active ? ' cur' : ''}" data-i="${i}">${bars(n.signal)}<span class="name">${esc(n.ssid)}</span>` +
    (n.active ? '<span class="meta">已连接</span>' : n.saved ? '<span class="meta saved">已保存</span>' : '') +
    (n.secure ? LOCK : '') + '</button>').join('');
}
$('wNets').addEventListener('click', e => {
  const b = e.target.closest('.w-net');
  if (b) wifiJoin(wifi.nets[Number(b.dataset.i)], false);
});

// mode: 'type' (enter a password), 'saved' (a saved network: connect or forget), 'info' (the one
// connected: forget). Saved networks wait for a tap on 连接 so 忘记 stays reachable.
function wifiJoin(net, hidden) {
  wifi.target = { ssid: net ? net.ssid : '', secure: net ? net.secure : true, hidden, saved: !!(net && net.saved),
                  mode: net && net.active ? 'info' : net && net.saved ? 'saved' : 'type' };
  wifi.ssid = wifi.target.ssid;
  wifi.pw = '';
  wifi.showPw = false;
  const t = wifi.target, q = net ? signalText(net.signal) : '';
  $('wJoinTitle').textContent = hidden ? '添加隐藏网络' : t.ssid;
  $('wJoinHint').textContent = hidden ? '输入网络名称和密码（没有密码就留空），然后点键盘上的「连接」'
    : t.mode === 'info' ? '已连接 ， ' + q
    : t.mode === 'saved' ? '已保存的网络 ， ' + q + '，点「连接」用保存的密码连接'
    : t.secure ? '加密网络 ， ' + q + '，输入密码后点键盘上的「连接」' : '开放网络 ， ' + q;
  wifiMsg('');
  wifiPane('wJoin');
  renderJoin();
  if (t.mode === 'type' && !t.secure && !hidden) wifiConnect();   // open network: nothing to type
  else wifiFocus();
}
const typing = () => wifi.target.mode === 'type';
// the system keyboard (injected by the kiosk shell) comes up with the focused field
function wifiFocus() {
  if (!typing() || wifi.busy) return;
  const t = wifi.target;
  const el = t.hidden && !wifi.ssid ? $('wSsid') : t.secure ? $('wPw') : $('wSsid');
  setTimeout(() => el.focus(), 50);
}
function canConnect() {
  const t = wifi.target;
  // a hidden network may be open: an empty password is allowed there, 1-7 characters never are
  const pwOk = t.hidden ? (wifi.pw.length === 0 || wifi.pw.length >= 8) : (!t.secure || wifi.pw.length >= 8);
  return !wifi.busy && (!t.hidden || wifi.ssid.length > 0) && pwOk;
}
function renderJoin() {
  const t = wifi.target;
  if ($('wSsid').value !== wifi.ssid) $('wSsid').value = wifi.ssid;
  if ($('wPw').value !== wifi.pw) $('wPw').value = wifi.pw;
  $('wPw').type = wifi.showPw ? 'text' : 'password';
  $('wPw').placeholder = t.hidden ? '没有密码就留空' : '至少 8 位';
  $('wSsid').disabled = $('wPw').disabled = wifi.busy;
  $('wEye').textContent = wifi.showPw ? '隐藏' : '显示';
  $('wFieldSsid').style.display = typing() && t.hidden ? '' : 'none';
  $('wFieldPw').style.display = typing() && t.secure ? '' : 'none';
  $('wForget').hidden = !(t.saved || t.mode === 'info');
  $('wJoinBtn').hidden = t.mode !== 'saved';
  $('wJoinBtn').disabled = $('wForget').disabled = wifi.busy;
}
function wifiMsg(text, kind) {
  $('wMsg').className = 'w-msg' + (kind ? ' ' + kind : '');
  $('wMsg').innerHTML = (kind === 'wait' ? '<span class="w-spin"></span>' : '') + esc(text);
}
async function wifiConnect() {
  const t = wifi.target;
  if (t.mode !== 'saved' && !canConnect()) return;
  wifi.busy = true;
  renderJoin();
  const usingSaved = t.mode === 'saved';
  wifiMsg(usingSaved ? `正在用已保存的密码连接 ${wifi.ssid}…` : `正在连接 ${wifi.ssid}…`, 'wait');
  wifi.connectId = shellSeq + 1;   // the id shellCall() is about to use: progress reports carry it
  const r = await shellCall('connect', { ssid: wifi.ssid, password: usingSaved || !t.secure ? '' : wifi.pw, hidden: !!t.hidden });
  wifi.connectId = null;
  wifi.busy = false;
  if (r.ok) {
    t.mode = 'info';
    t.saved = true;
    wifiMsg(`已连接 ${wifi.ssid}`, 'ok');
    renderJoin();
    setTimeout(() => { if (!wifi.busy && $('wJoin').classList.contains('on')) { wifiList(); wifiScan(); } }, 1500);
    return;
  }
  // whatever went wrong (the shell already went back to the previous network), let the user type a password next
  if (t.secure) t.mode = 'type';
  if (r.reason === 'password') wifi.pw = '';
  wifiMsg(usingSaved && r.reason === 'password' ? '保存的密码已失效，请重新输入'
    : WIFI_REASONS[r.reason] || ('连接失败' + (r.detail ? '：' + r.detail : '')) + '，已连回原来的网络', 'bad');
  renderJoin();
  wifiFocus();
}
window.thinksmartProgress = function (p) {
  if (wifi.busy && p.id === wifi.connectId && WIFI_STAGES[p.stage]) wifiMsg(WIFI_STAGES[p.stage], 'wait');
};
async function wifiForget() {
  if (wifi.busy) return;
  const ssid = wifi.target.ssid;
  wifi.busy = true;
  wifiMsg(`正在忘记 ${ssid}…`, 'wait');
  renderJoin();
  const r = await shellCall('forget', { ssid });
  wifi.busy = false;
  if (!r.ok) {
    wifiMsg(`没能忘记 ${ssid}，请再试一次`, 'bad');
    return renderJoin();
  }
  wifi.target.saved = false;
  wifiMsg(`已忘记 ${ssid}，下次连接需要重新输入密码`, 'ok');
  renderJoin();
  wifi.nets = wifi.nets.map(n => n.ssid === ssid ? Object.assign({}, n, { saved: false }) : n);
  setTimeout(() => { if (!wifi.busy) { wifiList(); wifiScan(); } }, 1400);
}
$('wifiBtn').addEventListener('click', openWifi);
$('wClose').addEventListener('click', closeWifi);
$('wScan').addEventListener('click', wifiScan);
$('wHidden').addEventListener('click', () => wifiJoin(null, true));
$('wBack').addEventListener('click', () => { if (!wifi.busy) wifiList(); });
$('wForget').addEventListener('click', wifiForget);
$('wJoinBtn').addEventListener('click', wifiConnect);
// On pointerdown, not click: cancelling a touch's pointerdown (to keep the field focused and the system
// keyboard up) makes WebKitGTK send no click at all, so a click handler never ran on the device's screen
// (a mouse in a desktop browser still clicks). The on-screen keyboard's keys work the same way.
$('wEye').addEventListener('pointerdown', e => { e.preventDefault(); wifi.showPw = !wifi.showPw; renderJoin(); });
$('wSsid').addEventListener('input', () => { wifi.ssid = $('wSsid').value; });
$('wPw').addEventListener('input', () => { wifi.pw = $('wPw').value; });
$('wSsid').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  e.preventDefault();   // keep the keyboard up: on to the password
  if (wifi.target.secure) $('wPw').focus();
});
$('wPw').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  if (!canConnect()) { e.preventDefault(); return; }   // keep typing
  wifiConnect();
});

/* ---- settings: Bluetooth speaker (bt.py; pairing only in the 3-minute "add a phone" window) ---- */
function btPlaying(d) {
  const song = [d.title, d.artist].filter(Boolean).join(' ， ');
  return song || '正在播放';
}
function renderBluetooth() {
  const b = ui.bluetooth;
  const on = !!(b && b.available && b.enabled);
  const live = b && b.enabled && (b.devices || []).find(d => d.streaming);
  document.body.classList.toggle('bt-playing', !!live);
  $('btChipText').textContent = live ? `${live.name} ， ${btPlaying(live)}` : '';
  if (!b) return;
  $('btSwitch').setAttribute('aria-checked', String(on));
  $('btSwitch').disabled = !b.available;
  $('btCard').classList.toggle('off', !on);
  $('btName').textContent = on && b.name ? `手机上显示为「${b.name}」` : '';
  let note;
  if (!b.available) note = '找不到蓝牙，或者蓝牙服务没有运行。';
  else if (!b.enabled) note = '打开后，手机可以把这台设备当蓝牙音箱放歌。';
  else if (b.window) note = `正在等手机连接（还剩 ${b.window} 秒）：在手机的蓝牙设置里点「${b.name}」，配对不用输密码。`;
  else note = '对话时自动降低手机音乐音量。';
  if (on && b.direct) note += ' 注意：系统层版本较旧，手机的声音没经过客户端，放歌时很难唤醒。';
  $('btNote').textContent = note;
  const phones = b.devices || [];
  $('btList').innerHTML = phones.length ? phones.map(d =>
    `<div class="al phone${d.streaming ? ' live' : ''}"><i>手机</i><div class="al-text"><b>${esc(d.name)}</b>` +
    `<span${d.streaming ? ' class="live"' : ''}>${d.streaming ? esc(btPlaying(d)) : d.connected ? '已连接' : '已配对，没连着'}</span></div>` +
    (btIsArmed(d.address) ? `<button class="al-del armed" data-addr="${esc(d.address)}">确认忘记</button></div>`
                          : `<button class="al-del" data-addr="${esc(d.address)}">忘记</button></div>`)).join('')
    : '<div class="device-empty">还没有连过的手机</div>';
  $('btAdd').classList.toggle('on', !!b.window);
  $('btAdd').textContent = b.window ? `停止等待（${b.window} 秒）` : '＋ 添加手机';
}
// 「忘记」 needs a second tap within 4 s. Kept by address, not on the button: while 添加手机 waits
// the list is redrawn every second (the countdown), which would drop a state kept on the DOM.
let btArmed = null;   // {addr, until}
const btIsArmed = addr => !!btArmed && btArmed.addr === addr && Date.now() < btArmed.until;
async function btPost(body) {
  if (DEMO !== null) {
    const b = ui.bluetooth;
    if ('enabled' in body) b.enabled = body.enabled;
    if ('window' in body) b.window = body.window ? 180 : 0;
    if (body.remove) b.devices = b.devices.filter(d => d.address !== body.remove);
    renderBluetooth();
    return { ok: true };
  }
  return postJson('/api/bluetooth', body);   // {ok: false} after it has shown the error
}
$('btSwitch').addEventListener('click', () => btPost({ enabled: !(ui.bluetooth && ui.bluetooth.enabled) }));
$('btAdd').addEventListener('click', () => btPost({ window: !(ui.bluetooth && ui.bluetooth.window) }));
$('btList').addEventListener('click', e => {
  const b = e.target.closest('.al-del');
  if (!b) return;
  if (!btIsArmed(b.dataset.addr)) {
    btArmed = { addr: b.dataset.addr, until: Date.now() + 4000 };
    renderBluetooth();
    setTimeout(renderBluetooth, 4100);
    return;
  }
  btArmed = null;
  btPost({ remove: b.dataset.addr }).then(r => { if (r && r.ok !== false) toast('已忘记，这台手机要重新配对才能连'); });
});


// 关机: halts the system (it stays dark with the power cable in), so unplugging can't damage it
let powerArmed = 0;
function renderPower() {
  $('powerBtn').textContent = Date.now() - powerArmed < 5000 ? '确认关机' : '关机';
  $('powerBtn').classList.toggle('armed', Date.now() - powerArmed < 5000);
}
$('powerBtn').addEventListener('click', async () => {
  if (Date.now() - powerArmed > 5000) {
    powerArmed = Date.now();
    renderPower();
    setTimeout(renderPower, 5100);
    return toast('关机后要拔掉电源，等 30 秒再插上才会开机，再点一下「确认关机」');
  }
  powerArmed = 0;
  renderPower();
  if (DEMO !== null) return toast('演示模式不会真的关机');
  const r = await (await post('/api/power-off', {}))?.json?.();
  if (!r || !r.ok) toast((r && r.error) || '没能关机', 'error');
});
$('settingsClose').addEventListener('click', () => {
  $('settings').classList.remove('on');
});
$('reportPill').addEventListener('click', () => { post('/api/reports/play'); toast('马上念给你听'); });

/* ---- right side: welcome screen or conversation ---- */
// The welcome screen (clock) when idle with nothing going on: no messages yet, or the conversation
// is over (the hub closed the connection). The hub keeps the history, so the thread is rarely empty;
// it comes back with the chat page on the next wake. While the connection lasts (~2 minutes) the
// chat page stays. A new conversation shows the chat (and its "listening" bubble) from the wake on.
function welcomeShown(messages, state, connected) {
  return ['idle', 'connecting'].includes(state) && (!messages || !connected);
}
function renderSide() {
  const hasMedia = ui.thread.messages.some(message => message.media && message.media.length);
  const page = ui.page || (!hasMedia && welcomeShown(ui.thread.messages.length, shownState(), !!ui.status.connected) ? 'dashboard' : 'chat');
  document.body.dataset.page = page;
  document.body.classList.toggle('fresh', page === 'dashboard');
  $('welcome').hidden = page !== 'dashboard';
  $('convo').hidden = page !== 'chat';
  $('pageTitle').textContent = page === 'dashboard' ? '仪表盘' : '对话';
  $('navDashboard').setAttribute('aria-current', page === 'dashboard' ? 'page' : 'false');
  $('navChat').setAttribute('aria-current', page === 'chat' ? 'page' : 'false');
  $('chatEmpty').hidden = ui.thread.messages.length > 0 || shownState() !== 'idle';
}

function setPage(page) {
  ui.page = page;
  renderSide();
  setHistoryOpen(false);
  if (page === 'chat') requestAnimationFrame(scrollDown);
}

function setHistoryOpen(open) {
  $('historyPanel').hidden = !open;
  $('navHistory').setAttribute('aria-expanded', String(open));
  if (open) { renderHistory(); loadHistoryList(); }
}

async function loadHistoryList() {
  if (DEMO !== null) return;
  const revision = historyRevision;
  try {
    const response = await fetch('/api/history', { cache: 'no-store' });
    if (!response.ok) throw new Error('history unavailable');
    const result = await response.json();
    if (revision !== historyRevision) return;
    for (const entry of Object.keys(seenThreads)) {
      if (entry !== ui.thread.entry) delete seenThreads[entry];
    }
    for (const [entry, messages] of Object.entries(result.threads || {})) {
      if (entry !== ui.thread.entry && Array.isArray(messages)) seenThreads[entry] = messages;
    }
    renderHistory();
  } catch (error) {
    if (!$('historyPanel').hidden) toast('记录暂时无法刷新', 'error');
  }
}

function renderHistory() {
  const query = $('historySearch').value.trim().toLocaleLowerCase();
  const list = $('historyList');
  list.replaceChildren();
  for (const model of ui.models.options) {
    const messages = model.entry === ui.thread.entry ? ui.thread.messages : seenThreads[model.entry] || [];
    const last = messages[messages.length - 1];
    const preview = last ? last.text || '媒体内容' : '打开对话';
    if (query && ![model.label, model.model, ...messages.map(message => message.text)].join(' ').toLocaleLowerCase().includes(query)) continue;
    const button = document.createElement('button');
    button.className = 'history-item';
    button.setAttribute('aria-current', String(model.entry === ui.thread.entry));
    button.innerHTML = `<img class="tile sm" src="${esc(logoSrc(model))}" alt=""><span><b>${esc(model.label)}</b><small>${esc(preview)}</small></span>`;
    button.addEventListener('click', () => { setPage('chat'); switchModel(model); });
    list.appendChild(button);
  }
  if (!list.childElementCount) {
    const empty = document.createElement('p');
    empty.className = 'history-empty';
    empty.textContent = query ? '没有匹配的记录' : '连接服务器后显示可用对话';
    list.appendChild(empty);
  }
}

function entryModel(entry) {
  return ui.models.options.find(o => o.entry === entry) || ui.models.current;
}

function renderHead() {
  const m = entryModel(ui.thread.entry);
  $('headLogo').src = logoSrc(m);
  $('headTitle').textContent = m ? `与 ${m.label} 的对话` : '对话';
  const n = ui.thread.messages.length;
  $('headMeta').textContent = n ? `${n} 条消息` : '';
}

function fmtTime(t) {
  const d = new Date(t * 1000);
  return pad(d.getHours()) + ':' + pad(d.getMinutes());
}

// A reply grows sentence by sentence; the text just shows as it is (no per-sentence highlight)
function fillMsg(el, m) {
  const html = esc(m.text) + (m.interrupted ? '<span class="cut">已打断</span>' : '') + `<span class="ts">${fmtTime(m.time)}</span>`;
  if (m.role === 'bot') {
    const body = el.querySelector('.body');
    let text = body.querySelector('.message-text');
    if (!text) {
      text = document.createElement('div');
      text.className = 'message-text';
      body.appendChild(text);
    }
    RichContent.renderText(text, m.text || '');
    let timestamp = body.querySelector(':scope > .ts');
    if (!timestamp) { timestamp = document.createElement('span'); timestamp.className = 'ts'; body.appendChild(timestamp); }
    timestamp.textContent = fmtTime(m.time) + (m.interrupted ? ' 已打断' : '');
    let choice = body.querySelector('.choice-card');
    if (m.choice) {
      if (!choice) { choice = document.createElement('section'); choice.className = 'choice-card'; body.appendChild(choice); }
      const version = JSON.stringify(m.choice);
      if (choice.dataset.card !== version) {
        choice.dataset.card = version;
        RichContent.renderChoice(choice, m.choice, async (id, option_id) => {
          if (DEMO !== null) return {ok: true, selected: option_id};
          const response = await fetch('/api/choice', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id, option_id})});
          const result = await response.json();
          if (!response.ok) throw new Error(result.error || '提交失败');
          return result;
        });
      }
    } else if (choice) choice.remove();
    const mediaKey = JSON.stringify(m.media || []);
    if (body.dataset.media !== mediaKey) {
      const previous = body.querySelector('.message-media');
      if (previous) { MediaCards.pauseAll(); previous.remove(); }
      const media = document.createElement('div');
      media.className = 'message-media';
      MediaCards.render(media, m.media || []);
      body.appendChild(media);
      body.dataset.media = mediaKey;
    }
  }
  else el.innerHTML = html;
}

function msgEl(m) {
  const el = document.createElement('div');
  el.className = 'msg ' + m.role;
  el.dataset.id = m.id;
  if (m.role === 'bot') {
    el.innerHTML = `<img class="tile" src="${esc(logoSrc(entryModel(m.entry)))}" alt=""><div class="body"></div>`;
  }
  fillMsg(el, m);
  return el;
}

function scrollDown() {
  const box = $('messages');
  box.scrollTop = box.scrollHeight;
}

function renderThread() {
  MediaCards.pauseAll();
  const box = $('messages');
  box.textContent = '';
  for (const m of ui.thread.messages) {
    const el = msgEl(m);
    el.style.animation = 'none';
    box.appendChild(el);
  }
  markLive();
  renderHead();
  renderSide();
  renderPending();
  scrollDown();
}

function upsertMessage(m) {
  if (m.entry !== ui.thread.entry) return;
  const i = ui.thread.messages.findIndex(x => x.id === m.id);
  const box = $('messages');
  const following = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  if (i >= 0) {
    ui.thread.messages[i] = m;
    const el = box.querySelector(`[data-id="${m.id}"]`);
    if (el) fillMsg(el, m);
  } else {
    ui.thread.messages.push(m);
    box.insertBefore(msgEl(m), $('pending'));
  }
  markLive();
  renderHead();
  renderSide();
  if (!$('historyPanel').hidden) renderHistory();
  if (following || m.role === 'user') scrollDown();
}

function markLive() {
  // the reply being spoken right now: its logo gets a ring
  const box = $('messages');
  box.querySelectorAll('.live').forEach(el => el.classList.remove('live'));
  const last = ui.thread.messages[ui.thread.messages.length - 1];
  if (ui.status.state === 'speaking' && last && last.role === 'bot') {
    const el = box.querySelector(`[data-id="${last.id}"]`);
    if (el) el.classList.add('live');
  }
}

// Placeholder at the end of the conversation while the user talks or the model thinks
function renderPending() {
  const shown = shownState();
  // recognising: the user stopped and the hub is turning it into text (it can take a few seconds)
  const kind = ui.status.recognising && !ui.ptt ? 'user recog' : shown === 'listening' ? (ui.ptt ? 'user ptt' : 'user') :
    shown === 'thinking' ? 'bot' : '';
  let el = $('pending');
  if (el && el.dataset.kind === kind) return;
  if (el) el.remove();
  if (!kind) return;
  el = document.createElement('div');
  el.id = 'pending';
  el.dataset.kind = kind;
  el.className = 'msg pending ' + kind;
  if (kind.startsWith('user')) {
    el.innerHTML = `<span class="wave">${'<b></b>'.repeat(5)}</span>${kind === 'user recog' ? '正在识别…' : ui.ptt ? '说完松开' : '正在听…'}`;
  } else {
    el.innerHTML = `<img class="tile" src="${esc(logoSrc(entryModel(ui.thread.entry)))}" alt="">` +
      `<div class="body"><span class="dots"><i></i><i></i><i></i></span><span class="label">正在思考</span></div>`;
  }
  $('messages').appendChild(el);
  scrollDown();
}

/* ---- overlays ---- */
let toastTimer = null;
function toast(text, kind) {
  const t = $('toast');
  t.textContent = text;
  t.className = 'toast on' + (kind === 'error' ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = 'toast' + (kind === 'error' ? ' error' : ''); }, kind === 'error' ? 4500 : 2500);
}

let hudTimer = null;
function showVolume(v) {
  $('hudFill').style.transform = `scaleX(${Math.max(0, Math.min(100, v)) / 100})`;
  $('hudNum').textContent = v;
  $('hud').classList.add('on');
  clearTimeout(hudTimer);
  hudTimer = setTimeout(() => $('hud').classList.remove('on'), 1600);
}

/* ---- events from the client ---- */
const handlers = {
  snapshot(s) {
    if (ui.build && s.build && s.build !== ui.build) return location.reload();   // client restarted (deployed)
    ui.build = s.build;
    ui.status = s.status; ui.models = server.models = s.models; ui.thread = server.thread = s.thread;
    switching = null;
    historyRevision += 1;
    for (const k in seenThreads) delete seenThreads[k];
    seenThreads[s.thread.entry] = s.thread.messages;
    if (s.bluetooth) ui.bluetooth = s.bluetooth;
    renderModels(); renderThread(); renderStatus(); renderBluetooth();
  },
  power(p) {
    $('powerOff').classList.toggle('on', p.state === 'off');
  },
  state(s) {
    if (s.state !== 'idle') MediaCards.pauseAll();
    if (s.state !== 'idle' && s.state !== ui.status.state && ui.status.state === 'idle') ui.page = 'chat';
    ui.status = s; renderStatus(); markLive(); renderHead();
  },
  model(m) {
    server.models = m;
    if (switching && (!m.current || m.current.entry !== switching)) return;
    ui.models = m; renderModels(); renderStatus();
  },
  thread(t) {
    server.thread = t;
    if (t.reset) {
      historyRevision += 1;
      for (const k in seenThreads) delete seenThreads[k];
    }
    seenThreads[t.entry] = t.messages;
    if (switching && t.entry !== switching) return;
    ui.thread = t; renderThread();
  },
  message: m => upsertMessage(m),
  media_pause: () => MediaCards.pauseAll(),
  woke: () => MediaCards.pauseAll(),
  toast: t => toast(t.text, t.kind),
  volume(v) {
    ui.status.volume = v.volume; renderVol();
    if (v.source !== 'page') showVolume(v.volume);
  },
  bluetooth(b) {
    ui.bluetooth = b; renderBluetooth();
  },
  level(l) {
    level.target = ui.status.state === 'speaking' ? Math.min(1, l.out * 6) : micLevel(l.mic);
  },
};

function connectEvents() {
  const es = new EventSource('/events');
  for (const [name, fn] of Object.entries(handlers)) es.addEventListener(name, e => fn(JSON.parse(e.data)));
}

/* ---- buttons ---- */
function post(path, body) {
  if (DEMO !== null) return demo.action(path, body);
  const opts = { method: 'POST' };
  if (body) Object.assign(opts, { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return fetch(path, opts).catch(() => toast('客户端没有响应', 'error'));
}

// Mic: a tap wakes (or interrupts a reply and listens); holding it is push-to-talk
let holdTimer = null;
function pttStart() {
  ui.ptt = true;
  $('btnMic').classList.add('hold');
  renderStatus();
}
function pttEnd() {
  ui.ptt = false;
  $('btnMic').classList.remove('hold');
  renderStatus();
}
$('btnMic').addEventListener('pointerdown', e => {
  if (e.button !== 0 || !e.isPrimary) return;
  e.preventDefault();
  e.currentTarget.setPointerCapture(e.pointerId);
  holdTimer = setTimeout(() => {
    holdTimer = null;
    pttStart();
    post('/api/ptt/start');
  }, 350);
});
function release() {
  if (holdTimer) {
    clearTimeout(holdTimer);
    holdTimer = null;
    post('/api/wake');
  } else if (ui.ptt) {
    pttEnd();
    post('/api/ptt/stop');
  }
}
$('btnMic').addEventListener('pointerup', release);
$('btnMic').addEventListener('pointercancel', () => {
  clearTimeout(holdTimer);
  holdTimer = null;
  if (ui.ptt) { pttEnd(); post('/api/ptt/stop'); }
});
$('btnMic').addEventListener('lostpointercapture', () => {
  clearTimeout(holdTimer);
  holdTimer = null;
  if (ui.ptt) { pttEnd(); post('/api/ptt/stop'); }
});
$('btnMic').addEventListener('click', e => { if (e.detail === 0) post('/api/wake'); });
$('btnAbort').addEventListener('click', () => post('/api/abort'));
$('btnEnd').addEventListener('click', () => { post('/api/end'); toast('对话已结束，聊天记录已保存'); });
$('modelPill').addEventListener('click', () => openSheet(true));
$('sheetClose').addEventListener('click', () => openSheet(false));
document.addEventListener('contextmenu', e => e.preventDefault());

$('navDashboard').addEventListener('click', () => setPage('dashboard'));
$('navChat').addEventListener('click', () => setPage('chat'));
$('navHistory').addEventListener('click', () => setHistoryOpen($('historyPanel').hidden));
$('navSettings').addEventListener('click', () => { setHistoryOpen(false); $('btnSettings').click(); });
$('historyClose').addEventListener('click', () => { setHistoryOpen(false); $('navHistory').focus(); });
$('historySearch').addEventListener('input', renderHistory);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !$('historyPanel').hidden) {
    setHistoryOpen(false);
    $('navHistory').focus();
  }
});

let sendingMessage = false;
async function sendText(text) {
  text = text.trim();
  if (!text || sendingMessage) return false;
  sendingMessage = true;
  $('sendMessage').disabled = true;
  $('composerStatus').textContent = '正在发送';
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    if (DEMO !== null) await demo.action('/api/text', { text });
    else {
      const response = await fetch('/api/text', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
        signal: controller.signal,
      });
      const result = await response.json();
      if (!response.ok || result.ok === false) throw new Error(result.error || '发送失败，请重试');
    }
    setPage('chat');
    $('composerStatus').textContent = '';
    return true;
  } catch (error) {
    $('composerStatus').textContent = '发送失败，内容已保留';
    toast(error.message || '客户端没有响应', 'error');
    return false;
  } finally {
    clearTimeout(timeout);
    sendingMessage = false;
    $('sendMessage').disabled = false;
  }
}
$('composer').addEventListener('submit', async event => {
  event.preventDefault();
  const input = $('messageInput');
  const draft = input.value;
  if (await sendText(draft) && input.value === draft) {
    input.value = '';
    $('composer').classList.remove('has-text');
  }
});
$('messageInput').addEventListener('input', () => {
  $('composer').classList.toggle('has-text', Boolean($('messageInput').value.trim()));
});
$('messageInput').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    $('composer').requestSubmit();
  }
});

for (const suggestion of SUGGEST) {
  const b = document.createElement('button');
  b.className = 'chip feature-' + suggestion.style;
  b.innerHTML = `<img class="feature-icon" src="/static/v2/icons/${suggestion.icon}.svg" alt=""><b>${esc(suggestion.title)}</b><span>${esc(suggestion.detail)}</span>`;
  b.addEventListener('click', () => sendText(suggestion.prompt));
  $('chips').appendChild(b);
}

/* ---- demo: fake client for previewing the page on a desktop browser ---- */
const demo = DEMO === null ? null : (() => {
  const M = (label, model, logo, company, vendor) => ({ kind: 'llm', vendor, label, model, logo, company, entry: 'llm:' + vendor });
  const options = [
    M('Claude', 'claude-sonnet-4-5', 'claude-color', 'Anthropic', 'claude'),
    M('DeepSeek', 'deepseek-chat', 'deepseek-color', 'DeepSeek', 'deepseek'),
    M('通义千问', 'qwen-max', 'qwen-color', '阿里通义', 'qwen'),
    M('豆包', 'doubao-1.5-pro', 'doubao-color', '字节豆包', 'doubao'),
    M('Gemini', 'gemini-2.5-flash', 'gemini-color', 'Google', 'gemini'),
    M('Kimi', 'kimi-k2', 'kimi-color', '月之暗面', 'kimi'),
    M('GPT', 'gpt-4.1', 'openai', 'OpenAI', 'openai'),
    M('智谱 GLM', 'glm-4.6', 'zhipu-color', '智谱', 'glm'),
  ];
  let seq = 0, bot = null, timer = null, stopped = false;
  const threads = {};
  const entry = () => ui.models.current.entry;
  const now = () => Date.now() / 1000;
  const state = (s, connected = true) => handlers.state({ state: s, connected, uploading: s === 'listening' });
  const say = (role, text) => {
    const m = { id: ++seq, role, text, time: now(), entry: entry() };
    (threads[m.entry] = threads[m.entry] || []).push(m);
    handlers.message({ ...m });
    return m;
  };
  const sentence = text => {
    if (!bot) { state('speaking'); bot = say('bot', text); return; }
    bot.text += text;
    handlers.message({ ...bot });
  };
  const done = (cut) => {
    if (bot && cut) { bot.interrupted = true; handlers.message({ ...bot }); }
    bot = null;
  };

  // fake microphone / speaker levels: syllable-like bursts
  setInterval(() => {
    const t = performance.now() / 1000;
    const v = Math.max(0, Math.abs(Math.sin(t * 6.3)) * (.55 + .45 * Math.sin(t * 1.7)) + (Math.random() - .5) * .25);
    handlers.level({ mic: v * .07, out: v * .17 });
  }, 100);

  const steps = [
    ['idle', 4000, () => {
      handlers.snapshot({ status: { state: 'idle', connected: false, uploading: false, volume: 60 },
                          bluetooth: { available: true, enabled: true, powered: true, window: 0, name: 'ThinkSmart View', direct: false,
                                       devices: [{ address: 'AA:BB:CC:DD:EE:01', name: '小张的 iPhone', paired: true, connected: true, streaming: true,
                                                   title: '晴天', artist: '周杰伦' },
                                                 { address: 'AA:BB:CC:DD:EE:02', name: 'Redmi K70', paired: true, connected: false, streaming: false }] },
                          models: { current: options[0], options }, thread: { entry: options[0].entry, messages: [] } });
    }],
    ['settings', 3000, () => $('settings').classList.add('on')],
    [null, 300, () => $('settings').classList.remove('on')],
    ['connecting', 1600, () => state('connecting', false)],
    ['listening', 3200, () => state('listening')],
    [null, 500, () => say('user', '明天北京天气怎么样？适合去爬山吗？')],
    ['thinking', 2600, () => state('thinking')],
    [null, 1700, () => sentence('明天北京晴转多云，气温 9 到 19 度，')],
    [null, 1700, () => sentence('北风二三级，空气质量良。')],
    ['speaking', 2200, () => sentence('这样的天气很适合去爬山，香山的红叶正是最好看的时候。')],
    [null, 2400, () => sentence('早晚温差比较大，记得带一件薄外套。')],
    [null, 2600, () => { done(); state('listening'); }],
    [null, 500, () => say('user', '那推荐一条人少一点的路线吧')],
    [null, 2000, () => state('thinking')],
    [null, 1800, () => sentence('可以试试从北京植物园进山，走樱桃沟到鬼笑石，')],
    ['chat', 2400, () => sentence('人比东门少很多，沿途还有溪水和水杉林。')],
    ['cut', 1400, () => { done(true); state('idle'); }],
    ['models', 3200, () => openSheet(true)],
    [null, 600, () => switchTo(options[1])],
    ['volume', 2400, () => showVolume(60)],
    ['ptt', 2600, () => { pttStart(); state('listening'); }],
    [null, 400, () => { pttEnd(); say('user', '用一句话介绍一下你自己'); state('thinking'); }],
    [null, 1800, () => sentence('我是小葵，住在这块屏幕里的语音助手，有问题随时叫我。')],
    [null, 3200, () => { done(); state('idle'); }],
    [null, 3000, () => { state('idle', false); toast('对话已结束，聊天记录已保存'); }],
  ];

  function switchTo(m) {
    openSheet(false);
    handlers.model({ current: m, options });
    handlers.thread({ entry: m.entry, messages: (threads[m.entry] || []).slice() });
    toast(`已切换到 ${m.label}`);
  }

  function run(i, jump) {
    if (stopped) return;
    const [name, wait, fn] = steps[i % steps.length];
    if (i % steps.length === 0) { seq = 0; for (const k in threads) delete threads[k]; }
    fn();
    if (jump && name === jump) return;          // ?demo=<scene>: stop on that scene
    if (jump) return run(i + 1, jump);
    timer = setTimeout(() => run(i + 1), wait);
  }

  // tapping around stops the script and lets the buttons do something plausible
  function action(path, body) {
    stopped = true;
    clearTimeout(timer);
    const answer = q => {
      say('user', q);
      state('thinking');
      timer = setTimeout(() => {
        sentence('这是演示模式，');
        timer = setTimeout(() => { sentence('真正的回答要连上客户端才有哦。'); timer = setTimeout(() => { done(); state('idle'); }, 2600); }, 1500);
      }, 1600);
    };
    if (path === '/api/wake') state('listening');
    else if (path === '/api/ptt/start') state('listening');
    else if (path === '/api/ptt/stop') answer('（按住说话的内容）');
    else if (path === '/api/text') answer(body.text);
    else if (path === '/api/abort') { done(true); state('idle'); }
    else if (path === '/api/end') { done(); state('idle', false); }
    return Promise.resolve();
  }

  return { start: () => run(0, DEMO || null), action, switchTo };
})();

tick();
setInterval(tick, 1000);
renderStatus();
renderThread();
if (demo) demo.start();
else connectEvents();
