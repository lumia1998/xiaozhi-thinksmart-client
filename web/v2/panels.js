const DisplayPanels = (() => {
  const body = document.getElementById('panelBody');
  const status = document.getElementById('panelStatus');
  let current = '', revision = 0, selected, events = [];
  const pending = new Set();
  const mic = document.getElementById('agendaMic'), editor = document.getElementById('agendaEditor');
  let recording = null, draftDay = '', saving = false;
  const formatDay = date => [date.getFullYear(), String(date.getMonth()+1).padStart(2,'0'), String(date.getDate()).padStart(2,'0')].join('-');
  const month = () => selected.slice(0,7);
  function element(tag, text, style) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (style) node.className = style;
    return node;
  }
  function button(label, click) {
    const node = element('button', label);
    node.type = 'button'; node.onclick = click;
    return node;
  }
  async function api(path, method='GET', data) {
    if (new URLSearchParams(location.search).has('demo')) throw Error('演示模式未连接设备');
    const response = await fetch('/api/panels/'+path, {method, cache:'no-store', headers:{'X-ThinkSmart-Panel':'1', ...(data ? {'Content-Type':'application/json'} : {})}, body:data ? JSON.stringify(data) : undefined});
    const result = await response.json();
    if (!response.ok) throw Error(result.error || '面板暂不可用');
    return result;
  }
  function weather(data) {
    const hero = element('div', null, 'weather-now');
    hero.append(element('h2', data.place), element('strong', data.temperature == null ? '—' : Math.round(data.temperature)+'°'), element('span', data.condition));
    if (data.humidity != null) hero.append(element('small', '湿度 '+data.humidity+'%'));
    const strip = element('div', null, 'forecast');
    for (const day of data.days) {
      const row = element('div');
      row.append(element('b', day.date.slice(5).replace('-', '/')), element('span', day.condition), element('small', `${day.low ?? '—'}° / ${day.high ?? '—'}°`));
      strip.append(row);
    }
    body.replaceChildren(hero, element('h2','逐日预报'), strip);
  }
  function devices(data) {
    body.replaceChildren();
    if (!data.devices.length) { body.append(element('p','暂无已授权的设备')); return; }
    const rooms = new Map();
    for (const device of data.devices) {
      if (!rooms.has(device.room)) {
        const grid = element('div', null, 'device-grid'); rooms.set(device.room, grid);
        body.append(element('h2', device.room || '未分房间'), grid);
      }
      const card = button('', async () => {
        if (pending.has(device.id)) return;
        const view = revision;
        pending.add(device.id); card.disabled = true; status.textContent = '正在等待设备确认';
        let message;
        try { const result = await api('devices','POST',{id:device.id,on:!device.on}); message = result.message; }
        catch (error) { message = error.message; }
        finally { pending.delete(device.id); }
        if (current === 'devices') await refresh(view === revision ? message : '');
      });
      const state = device.on == null ? '状态未知' : device.on ? '已开启' : '已关闭';
      card.className = 'device-card'; card.dataset.on = String(device.on === true);
      card.disabled = !device.writable || pending.has(device.id);
      card.setAttribute('aria-label', device.room+device.name+'，'+state);
      if (device.on != null) { card.setAttribute('role','switch'); card.setAttribute('aria-checked',String(device.on)); }
      card.append(element('b',device.name),element('small',state+(device.writable || device.on == null ? '' : '，只读')),element('i',null,'power-track'));
      rooms.get(device.room).append(card);
    }
  }
  function calendar() {
    const [year, number, day] = selected.split('-').map(Number);
    const layout = element('div',null,'agenda-layout');
    const picker = element('section'), details = element('section',null,'agenda-details');
    const switchMonth = offset => { selected = formatDay(new Date(year,number-1+offset,1,12)); refresh(); };
    const header = element('div',null,'month-bar');
    const prev = button('‹',()=>switchMonth(-1)), next = button('›',()=>switchMonth(1));
    prev.setAttribute('aria-label','上个月'); next.setAttribute('aria-label','下个月');
    header.append(prev, element('h2',`${year}年${number}月`), next);
    const grid = element('div',null,'month-grid');
    for (const name of '一二三四五六日') grid.append(element('small',name));
    const start = (new Date(year,number-1,1,12).getDay()+6)%7, last = new Date(year,number,0,12).getDate();
    for (let blank=0;blank<start;blank++) grid.append(element('span'));
    for (let date=1;date<=last;date++) {
      const key = month()+'-'+String(date).padStart(2,'0'), has = events.some(item=>item.date===key);
      const cell = button(date,()=>{cancelVoice(); selected=key; calendar(); document.getElementById('date-'+key)?.focus();});
      cell.id='date-'+key; cell.dataset.events=String(has); cell.setAttribute('aria-pressed',String(date===day));
      cell.setAttribute('aria-label', `${number}月${date}日${has ? '，有日程' : ''}`); grid.append(cell);
    }
    picker.append(header,grid);
    details.append(element('h2',`${number}月${day}日  星期${'日一二三四五六'[new Date(year,number-1,day,12).getDay()]}`));
    const entries = events.filter(item=>item.date===selected);
    if (!entries.length) details.append(element('p','这一天没有日程'));
    const mutate = async (method, path, data, control) => {
      if (pending.has('agenda')) return;
      const view = revision;
      pending.add('agenda'); control.disabled=true;
      try { await api(path,method,data); if (current==='agenda') await refresh(); }
      catch(error) { if(view===revision) status.textContent=error.message; }
      finally { pending.delete('agenda'); control.disabled=false; }
    };
    const timeline = element('ol',null,'agenda-timeline');
    for (const item of entries) {
      const row = element('li',null,'agenda-row'), remove = button('删除',()=>mutate('DELETE',`agenda/${item.id}?month=${month()}`,null,remove));
      remove.setAttribute('aria-label','删除日程：'+item.title);
      const icon = element('span',null,'event-icon'); icon.setAttribute('aria-hidden','true');
      const paths = /起床|闹钟/.test(item.title) ? '<circle cx="12" cy="13" r="8"/><path d="M12 8v5l3 2M3 6l4-4m10 0 4 4M6 20l-2 2m14-2 2 2"/>' : /电视/.test(item.title) ? '<rect x="2" y="4" width="20" height="14" rx="2"/><path d="M8 21h8"/>' : /吃药|服药/.test(item.title) ? '<path d="M4 20a5 5 0 0 1 0-7l9-9a5 5 0 0 1 7 7l-9 9a5 5 0 0 1-7 0zm4-11 7 7"/>' : '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18"/>';
      icon.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">${paths}</svg>`;
      const detail=element('details'), summary=element('summary',item.title); detail.append(summary,remove);
      row.append(element('time',item.time||'全天'),icon,detail); timeline.append(row);
    }
    details.append(timeline); layout.append(picker,details); body.replaceChildren(layout);
  }
  function openDraft(data={}) {
    if(editor.open || current!=='agenda') return;
    draftDay=selected;
    document.getElementById('agendaDraftDate').textContent=draftDay;
    document.getElementById('agendaName').value=data.title||'';
    document.getElementById('agendaTime').value=data.time||'';
    document.getElementById('agendaDraftError').textContent='';
    editor.showModal();
  }
  document.getElementById('agendaDiscard').onclick=()=>{if(!saving) editor.close();};
  editor.oncancel=event=>{if(saving) event.preventDefault();};
  document.getElementById('agendaDraft').onsubmit=async event=>{
    event.preventDefault(); if(saving) return;
    const title=document.getElementById('agendaName').value.trim();
    if(!title) return;
    saving=true;
    try {await api('agenda?month='+draftDay.slice(0,7),'POST',{date:draftDay,time:document.getElementById('agendaTime').value,title});
      editor.close(); if(current==='agenda') await refresh();
    } catch(error){document.getElementById('agendaDraftError').textContent=error.message;} finally{saving=false;}
  };
  function micLabel(label){mic.querySelector('span').textContent=label; mic.dataset.active=String(label==='松开完成');}
  const voiceCall=(action,token)=>api('agenda-voice','POST',{action,token});
  function cancelVoice(){
    const take=recording; if(!take) return;
    recording=null; clearTimeout(take.timer); micLabel('按住说日程');
    if(!take.ending) take.start.then(result=>voiceCall('cancel',result.token)).catch(()=>{});
  }
  function beginVoice(){
    if(recording || editor.open || current!=='agenda') return;
    status.textContent=''; micLabel('松开完成');
    const take={start:voiceCall('start'), ending:false}; recording=take;
    take.timer=setTimeout(endVoice,9800);
    take.start.catch(error=>{if(recording===take){cancelVoice();status.textContent=error.message;}});
  }
  async function endVoice(){
    const take=recording; if(!take || take.ending) return;
    take.ending=true; clearTimeout(take.timer); micLabel('正在识别…');
    try {const {token}=await take.start;
      if(recording!==take){await voiceCall('cancel',token);return;}
      const result=await voiceCall('finish',token);
      if(recording===take) openDraft(result);
    } catch(error){if(recording===take) status.textContent=error.message;}
    finally{if(recording===take){recording=null;micLabel('按住说日程');}}
  }
  mic.onpointerdown=event=>{if(!event.isPrimary||event.button!==0)return;event.preventDefault();mic.setPointerCapture(event.pointerId);beginVoice();};
  mic.onpointerup=event=>{if(event.isPrimary)endVoice();};
  mic.onpointercancel=cancelVoice; mic.onlostpointercapture=()=>{if(!recording?.ending)cancelVoice();};
  mic.onkeydown=event=>{if([' ','Enter'].includes(event.key)){event.preventDefault();if(!event.repeat)beginVoice();}};
  mic.onkeyup=event=>{if([' ','Enter'].includes(event.key)){event.preventDefault();endVoice();}};
  mic.oncontextmenu=event=>event.preventDefault();
  window.addEventListener('blur',cancelVoice); document.addEventListener('visibilitychange',()=>{if(document.hidden)cancelVoice();});
  async function refresh(message='') {
    cancelVoice();
    const view=++revision, page=current;
    if(!page) return;
    body.replaceChildren(); status.textContent='正在读取…';
    try {
      const data=await api(page==='agenda' ? 'agenda?month='+month() : page);
      if(view!==revision) return;
      if(page==='weather') weather(data); else if(page==='devices') devices(data); else {events=data.events; calendar();}
      status.textContent=message;
    } catch(error) { if(view===revision) status.textContent=[message,error.message].filter(Boolean).join('。'); }
  }
  function navigate(panel) {
    if(current===(panel?.id||'')) return;
    cancelVoice(); if(editor.open) editor.close();
    current=panel?.id||''; revision++;
    mic.hidden=current!=='agenda';
    const action=document.getElementById('panelRefresh');
    action.textContent=current==='agenda'?'手动添加':'刷新';
    action.onclick=current==='agenda'?()=>{cancelVoice();openDraft();}:()=>refresh();
    document.getElementById('panelBack').textContent=current==='agenda'?'‹':'‹ 首页';
    document.getElementById('panelBack').setAttribute('aria-label','返回首页');
    if(!panel) return;
    if(current==='agenda') selected=formatDay(new Date());
    const title=document.getElementById('panelTitle'); title.textContent=current==='agenda'?'日程':panel.title; title.focus();
    refresh();
  }
  return {navigate,refresh};
})();
