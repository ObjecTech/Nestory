/** Real HTTP integration: no frontend Mock, real login/CSRF, temporary backend SQLite. */
const assert = require('node:assert/strict');
require('./assistant_dom.cjs');
const origin = process.env.M1_ORIGIN;
if (!origin) throw new Error('Run through python unittest test_assistant_m1 with an isolated server');
window.location.origin = origin;
window.location.hostname = '127.0.0.1';
let redirect = null;
window.location.assign = path => { redirect = path; };
const realFetch = global.fetch;
let cookie;
let observed = new Set();
async function login() {
  const r = await realFetch(origin+'/login', {method:'POST', redirect:'manual',
    headers:{Origin:origin, 'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams({username:'assistant-dev', password:process.env.M1_PASSWORD})});
  assert.equal(r.status,303);
  cookie = r.headers.getSetCookie().map(s=>s.split(';')[0]).join('; ');
  document.cookie = cookie.split('; ').filter(s=>s.startsWith('bmon_csrf=')).join('; ');
}
global.fetch = async (path, options={}) => {
  observed.add(`${options.method||'GET'} ${new URL(path,origin).pathname.replace(/\/[a-f0-9-]{36}/g, '/:id')}`);
  return realFetch(new URL(path,origin), {...options, headers:{...options.headers, Cookie:cookie, Origin:origin}});
};
const transport = global.fetch;
const A = require('../static/assistant.js');
const api = A.assistantApi;
const wait = ms=>new Promise(r=>setTimeout(r,ms));
async function settled(cid=A.state.activeConversationId) {
  for (let i=0;i<160;i++) {
    if (!A.state.activePolls.has(cid)) return;
    await wait(25);
  }
  throw new Error('Polling failed to settle');
}
async function directTurn(cid, content, id, scope='demo') {
  const accepted = await api(`/api/assistant/conversations/${cid}/messages?scope=${scope}`, {
    method:'POST',body:JSON.stringify({content,client_message_id:id,timezone:'Asia/Shanghai'})});
  if (['completed','failed'].includes(accepted.status)) return accepted;
  for(let i=0;i<80;i++) {
    const turn=await api(accepted.poll_url);
    if(['completed','failed'].includes(turn.status)) return turn;
    await wait(10);
  }
  throw new Error('Backend completion timed out');
}
const day = process.env.M1_DAY || new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const eventWindow = `from=${day}&to=${day}&timezone=Asia%2FShanghai`;
const local11 = new Date(day+'T11:00:00+08:00').toISOString();
async function business() {
  await A.init(); assert.equal(A.state.isMock,false); assert.equal(A.state.conversations.length,0);
  // UI defaults to the single real record set; this flow exercises the demo scope explicitly.
  assert.equal(A.state.scope,'real'); await A.setScope('demo');
  await A.createConversation(); const ca=A.state.activeConversationId;
  await A.renameConversation(ca,'M1_A');
  const input=document.getElementById('assistantInput');
  input.value='今天上午 11 点宝宝喝了 150ml 奶';
  await A.sendMessage(input.value, false, 'm1-milk');
  assert.equal(A.state.messages.length,1); assert.equal(A.state.messages[0].status,'queued');
  assert.ok(A.state.activePolls.has(ca));
  await settled();
  const reply=A.state.messages.find(m=>m.role==='assistant');
  assert.equal(reply.record_actions[0].status,'saved');
  const saved=reply.record_actions[0].event; assert.equal(saved.amount_ml,150); assert.equal(saved.occurred_at.slice(0,19),local11.slice(0,19));
  await A.selectConversation(ca); // real history + per-turn hydration
  assert.equal(A.state.messages.find(m=>m.role==='assistant').record_actions[0].event.id,saved.id);
  const replay=await directTurn(ca,'今天上午 11 点宝宝喝了 150ml 奶','m1-milk');
  assert.equal(replay.replayed,true); assert.equal(replay.record_actions[0].event.id,saved.id);
  let events=await A.loadCareEvents(day,day); assert.equal(events.length,1);
  await A.createConversation(); const cb=A.state.activeConversationId;
  await A.renameConversation(cb,'M1_B');
  await A.sendMessage('查询今天喂奶记录'); await settled();
  assert.equal(A.state.messages.find(m=>m.role==='assistant').facts.parent_reported_ml,150);
  assert.equal(A.state.messages.find(m=>m.role==='assistant').facts.feeding_count,1);
  assert.ok(!A.state.messages.some(m=>m.content==='今天上午 11 点宝宝喝了 150ml 奶'));
  A.state.initialized=false; await A.init(); assert.equal(A.state.activeConversationId,cb);
  let updated=await A.updateCareEvent(saved.id,{amount_ml:155,notes:'M1_CROSS_SESSION'},1);
  assert.equal(updated.version,2);
  await assert.rejects(A.updateCareEvent(saved.id,{amount_ml:160},1),e=>e.status===409 && e.code==='version_conflict');
  await A.deleteConversation(ca);
  await assert.rejects(api(`/api/assistant/conversations/${ca}/messages?scope=demo`),e=>e.status===404);
  events=await A.loadCareEvents(day,day); assert.equal(events[0].id,saved.id); assert.equal(events[0].source_message_id,null);
  await A.deleteCareEvent(saved.id,2);
  await A.sendMessage('查询今天喂奶记录'); await settled();
  assert.equal(A.state.messages.filter(m=>m.role==='assistant').at(-1).facts.feeding_count,0);
  assert.equal((await A.loadCareEvents(day,day)).length,0);
  // Incomplete event: no write before user confirms; use the actual rendered confirmation button.
  await A.sendMessage('今天上午11点宝宝喝了一些奶'); await settled();
  let pending=A.state.messages.filter(m=>m.role==='assistant').at(-1);
  assert.equal(pending.record_actions[0].status,'needs_confirmation'); assert.equal(pending.record_actions[0].event,null);
  assert.equal((await A.loadCareEvents(day,day)).length,0);
  const area=document.getElementById('assistantMessagesArea');
  const card=area.querySelectorAll('.record-confirm-box').at(-1);
  const amount=card.querySelectorAll('input').find(i=>i.type==='number'); amount.value='130';
  card.querySelector('.record-btn-confirm').click();
  for(let i=0;i<80 && pending.record_actions[0].status==='needs_confirmation';i++) await wait(10);
  assert.equal(pending.record_actions[0].status,'saved'); assert.equal(pending.record_actions[0].event.amount_ml,130);
  const pendingId=pending.record_actions[0].event.id;
  await A.updateCareEvent(pendingId,{notes:'M1_PENDING_CONFIRMED'},1);
  await A.confirmRecordAction(pending.record_actions[0].action_id,pending.user_message_id,'confirm',{amount_ml:130,occurred_at:local11});
  assert.equal((await A.loadCareEvents(day,day)).length,1);
  // Real isolated scope: distinct archive, same event type, wrong-scope opaque IDs fail.
  await A.setScope('real'); assert.equal(A.state.messages.length,0); assert.equal(A.state.conversations.length,0);
  await A.createConversation('real'); const realConv=A.state.activeConversationId;
  const manual={event_type:'feeding',occurred_at:local11,amount_ml:60,notes:'M1_MANUAL',client_event_id:'m1-form'};
  const realEvent=await A.createManualCareEvent(manual);
  const duplicate=await A.createManualCareEvent(manual); assert.equal(realEvent.id,duplicate.id);
  assert.equal((await A.loadCareEvents(day,day)).length,1);
  await assert.rejects(api(`/api/assistant/care-events/${realEvent.id}?scope=demo`,{method:'PATCH',body:JSON.stringify({expected_version:1,notes:'cross'})}),e=>e.status===404);
  await assert.rejects(api(`/api/assistant/conversations/${realConv}/messages?scope=demo`),e=>e.status===404);
  assert.equal((await api('/api/assistant/sleep-summary?scope=demo&'+eventWindow)).scope,'demo');
  await A.setScope('demo'); assert.ok(A.state.conversations.every(c=>c.scope==='demo'));
  // Delayed turn finishes in background without entering the selected B message list.
  await A.createConversation(); const cd=A.state.activeConversationId;
  await A.sendMessage('M1_DELAY background reply'); await A.selectConversation(cb); await settled(cd);
  assert.ok(!A.state.messages.some(m=>m.content.includes('M1_DELAY')));
  await A.selectConversation(cd); assert.equal(A.state.messages.length,2);
  // Delete while mock inference is running: no recreated conversation/messages/events.
  await A.createConversation(); const doomed=A.state.activeConversationId;
  await A.sendMessage('M1_DELAY delete while running');
  const during=await api(A.state.activePolls.get(doomed).pollUrl); assert.equal(during.status,'running');
  await A.deleteConversation(doomed); await wait(450);
  await assert.rejects(api(`/api/assistant/conversations/${doomed}/messages?scope=demo`),e=>e.status===404);
  // Mock faults pass through real turn persistence and HTTP 200 failed polling.
  await A.selectConversation(cb);
  for(const [content,code] of [['M1_MODEL_FAILURE','model_failed'],['M1_MODEL_TIMEOUT','model_timeout']]) {
    await A.sendMessage(content); await settled();
    const msg=A.state.messages.at(-1); assert.equal(msg.role,'user'); assert.equal(msg.status,'failed'); assert.equal(msg.error.code,code);
    await A.selectConversation(cb); assert.equal(A.state.messages.at(-1).error.code,code);
  }
  // Real CSRF/Origin/session rejection: no client-side fallback.
  const csrf=document.cookie; document.cookie='';
  await assert.rejects(api('/api/assistant/conversations',{method:'POST',body:'{"scope":"demo"}'}),e=>e.status===403 && e.code==='csrf_failed'); document.cookie=csrf;
  const badOrigin=await realFetch(origin+'/api/assistant/conversations',{method:'POST',headers:{Cookie:cookie,Origin:'https://invalid.example','Content-Type':'application/json','X-CSRF-Token':csrf.split('=')[1]},body:'{"scope":"demo"}'});
  assert.equal(badOrigin.status,403);
  const goodCookie=cookie; cookie=''; await assert.rejects(api('/api/assistant/conversations?scope=demo'),e=>e.status===401); assert.equal(redirect,'/login'); cookie=goodCookie;
  // Each HTTP/network failure leaves Mock disabled, no invented persistence acknowledgement.
  const mockCount=A.mockStore.demo.care_events.length;
  for(const status of [404,501,503]) {
    global.fetch=async()=>({ok:false,status,json:async()=>({api_version:'1',error:{code:'injected',message:'实际错误'}})});
    await assert.rejects(api('/api/assistant/conversations?scope=demo'),e=>e.status===status);
    assert.equal(A.state.isMock,false);
  }
  global.fetch=async()=>{throw new TypeError('Failed to fetch');};
  await assert.rejects(api('/api/assistant/conversations?scope=demo'),e=>e.code==='network_error'); assert.equal(A.state.isMock,false); assert.equal(A.mockStore.demo.care_events.length,mockCount);
  global.fetch=transport;
  // Lost HTTP acceptance response: retain draft/client ID; repeat does not write twice.
  await A.createConversation(); const lost=A.state.activeConversationId; let firstId;
  global.fetch=async(path,options)=>{const r=await transport(path,options); if(options?.method==='POST' && path.includes('/messages?')) {firstId=JSON.parse(options.body).client_message_id;throw new TypeError('lost response');} return r;};
  input.value='今天上午11点宝宝喝了80ml奶'; await A.sendMessage(input.value);
  assert.equal(input.value,'今天上午11点宝宝喝了80ml奶');
  global.fetch=transport; await A.sendMessage(input.value); await settled(lost);
  assert.equal(A.state.messages.find(m=>m.role==='user').client_message_id,firstId);
  assert.equal((await A.loadCareEvents(day,day)).filter(e=>e.amount_ml===80).length,1);
  // Late history read from Demo cannot populate Real view.
  let release;
  global.fetch=async(path,options)=>{const r=await transport(path,options); if(path.includes(`/conversations/${lost}/messages?`)) await new Promise(resolve=>release=resolve); return r;};
  const selecting=A.selectConversation(lost); for(let i=0;i<50&&!release;i++) await wait(5);
  await A.setScope('real'); release(); await selecting; assert.equal(A.state.activeConversationId,realConv); assert.ok(A.state.messages.every(m=>m.conversation_id===realConv)); global.fetch=transport;
  // Deleting background D must not cancel an in-flight read of the selected B.
  await A.setScope('demo');
  let releaseD,releaseB;
  global.fetch=async(path,options)=>{const r=await transport(path,options);
    if(path.includes(`/conversations/${cd}/messages?`)) await new Promise(resolve=>releaseD=resolve);
    if(path.includes(`/conversations/${cb}/messages?`)) await new Promise(resolve=>releaseB=resolve);
    return r;};
  const readD=A.selectConversation(cd); for(let i=0;i<50&&!releaseD;i++) await wait(5);
  const readB=A.selectConversation(cb); for(let i=0;i<50&&!releaseB;i++) await wait(5);
  await A.deleteConversation(cd); releaseD(); releaseB(); await Promise.all([readD,readB]);
  assert.equal(A.state.activeConversationId,cb); assert.ok(A.state.messages.length>0);
  global.fetch=transport;
  // All 13 frozen routes were exercised through HTTP, including confirmation and standalone sleep.
  assert.equal(observed.size,13, [...observed].join('\n'));
  // Collection cursors and restoring a valid selection outside the first 50 rows.
  await A.setScope('demo');
  const paged = (await api('/api/assistant/conversations',{method:'POST',body:'{"scope":"demo"}'})).conversation.id;
  for(let i=0;i<27;i++) await directTurn(paged,`M1_HISTORY_${i}`,`history-${i}`);
  for(let i=0;i<52;i++) await api('/api/assistant/conversations',{method:'POST',body:'{"scope":"demo"}'});
  localStorage.setItem('nestory_conv_demo',cb); A.state.initialized=false; await A.init();
  assert.equal(A.state.activeConversationId,cb); assert.equal(A.state.conversationsHasMore,true);
  const previousCount=A.state.conversations.length;
  document.getElementById('assistantConvList').querySelectorAll('.assistant-btn-text').at(-1).click();
  for(let i=0;i<100&&A.state.conversations.length===previousCount;i++) await wait(5);
  assert.ok(A.state.conversations.some(c=>c.id===paged));
  await A.selectConversation(paged); assert.equal(A.state.messages.length,50); assert.equal(A.state.messagesHasMore,true);
  document.getElementById('assistantMessagesArea').querySelector('.assistant-btn-text').click();
  for(let i=0;i<100&&A.state.messages.length===50;i++) await wait(5);
  assert.equal(A.state.messages.length,54); assert.equal(A.state.messages[0].content,'M1_HISTORY_0');
  for(let i=0;i<51;i++) await api('/api/assistant/care-events',{method:'POST',body:JSON.stringify({scope:'demo',timezone:'Asia/Shanghai',client_event_id:`page-care-${i}`,event_type:'feeding',occurred_at:local11,amount_ml:10})});
  document.getElementById('careEventFromDate').value=day; document.getElementById('careEventToDate').value=day;
  document.getElementById('assistantCareEventsBtn').click();
  const careList=document.getElementById('careEventsList');
  for(let i=0;i<100&&careList.querySelectorAll('.care-event-item').length!==50;i++) await wait(5);
  assert.equal(careList.querySelectorAll('.care-event-item').length,50);
  careList.querySelector('.assistant-btn-text').click();
  for(let i=0;i<100&&careList.querySelectorAll('.care-event-item').length===50;i++) await wait(5);
  assert.equal(careList.querySelectorAll('.care-event-item').length,53);
  console.log('M1 collection pagination: conversations >50, messages >50, care events >50 and saved selection restore PASSED');
  console.log('M1 real HTTP: 13 API routes; conversation/event/SQLite lifecycle; confirmation; idempotency; faults; authorization; scope/race isolation PASSED');
}
async function restart() {
  await A.init(); assert.equal(A.state.isMock,false);
  await A.setScope('demo');
  if (A.state.conversationsHasMore) {
    const count=A.state.conversations.length;
    document.getElementById('assistantConvList').querySelector('.assistant-btn-text').click();
    for(let i=0;i<100&&A.state.conversations.length===count;i++) await wait(5);
  }
  assert.ok(A.state.conversations.some(c=>c.title==='M1_B'));
  assert.ok(!A.state.conversations.some(c=>c.title==='M1_A'));
  let events=await A.loadCareEvents(day,day); if(events.has_more) events=events.concat(await A.loadCareEvents(day,day,'all',events.next_cursor)); assert.ok(events.some(e=>e.notes==='M1_PENDING_CONFIRMED')); assert.ok(!events.some(e=>e.notes==='M1_CROSS_SESSION'));
  await A.setScope('real'); assert.equal((await A.loadCareEvents(day,day)).find(e=>e.notes==='M1_MANUAL').amount_ml,60);
  console.log('M1 actual app/service restart + re-login: scoped conversations, messages and care records persisted PASSED');
}
(async()=>{await login(); await (process.env.M1_STAGE==='restart'?restart():business());})().then(()=>process.exit(0)).catch(e=>{console.error(e);process.exit(1);});
