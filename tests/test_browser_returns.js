const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto').webcrypto;
const Providers = require('../aiflame/integrations/chatgpt_web/extension/providers.js');
const script = fs.readFileSync(path.join(__dirname, '../aiflame/integrations/chatgpt_web/extension/returns.js'), 'utf8');
const source = 'chatgpt-web', url = 'https://chatgpt.com/c/conversation-one';

function fixture() {
  const local = {}, session = {}, calls = [];
  const storage = values => ({get:async key => key ? {[key]:values[key]} : {...values}, set:async patch => Object.assign(values, patch)});
  const chrome = {storage:{local:storage(local), session:storage(session)}, runtime:{},
    tabs:{get:async id => ({id,windowId:8}), sendMessage:async()=>({source_id:source,url}),
      update:async(...args)=>calls.push(['select',...args]), create:async props => {calls.push(['create',props]); return {id:90,windowId:8};}},
    windows:{update:async(...args)=>calls.push(['focus',...args])}};
  const context={chrome,crypto,URL,Date,FlameProviders:Providers,setTimeout,clearTimeout};
  vm.runInNewContext(script,context);
  const event={task_id:crypto.randomUUID(),source_id:source,state:'working'};
  const sender={tab:{id:4,windowId:8},documentId:'doc',url};
  const command=()=>({request_id:crypto.randomUUID(),locator_id:event.task_id,source_id:source});
  return {api:context.FlameReturns,event,sender,local,session,calls,chrome,command};
}

test('completed tasks keep original tab and profile; current window is resolved at click time', async()=>{
  const f=fixture();
  const registered=await f.api.remember(f.event,f.sender,url);
  await f.api.remember({...f.event,state:'done'},f.sender,url);
  assert.equal(registered.return_target.kind,'web');
  f.chrome.tabs.get=async id=>({id,windowId:99});
  assert.equal(await f.api.open(f.command()),'opened');
  assert.deepEqual(f.calls.map(c=>c[0]),['select','focus']);
  assert.equal(f.calls[0][1],4);assert.equal(f.calls[1][1],99);
});
test('same URL in two tabs retains each actual producer', async()=>{
  const f=fixture();await f.api.remember(f.event,f.sender,url);
  const second={...f.event,task_id:crypto.randomUUID()};
  await f.api.remember(second,{...f.sender,tab:{id:5}},url);
  await f.api.open(f.command());
  await f.api.open({...f.command(),locator_id:second.task_id});
  assert.deepEqual(f.calls.filter(c=>c[0]==='select').map(c=>c[1]),[4,5]);
});
test('changed conversation or closed tab reopens saved conversation in owning profile',async()=>{
  for(const closed of [false,true]) {
    const f=fixture();await f.api.remember(f.event,f.sender,url);
    f.chrome.tabs.sendMessage=async()=>({source_id:source,url:'https://chatgpt.com/c/another'});
    if(closed)f.chrome.tabs.get=async()=>{throw Error('closed');};
    assert.equal(await f.api.open(f.command()),'opened');
    assert.equal(f.calls[0][0],'create');assert.equal(f.calls[0][1].url,url);
  }
});
test('browser restart never selects old tab IDs and profile identities stay separate',async()=>{
  const f=fixture(), other=fixture();
  const first=await f.api.remember(f.event,f.sender,url), second=await other.api.remember(other.event,other.sender,url);
  assert.notEqual(first.return_target.client_id,second.return_target.client_id);
  delete f.session.seeend_return_epoch;
  const context={chrome:f.chrome,crypto,URL,Date,FlameProviders:Providers,setTimeout,clearTimeout};
  vm.runInNewContext(script,context);
  assert.equal(await context.FlameReturns.open(f.command()),'opened');
  assert.equal(f.calls[0][0],'create');
});
test('new conversation commits update URL; navigation cancellation preserves original',async()=>{
  const f=fixture();await f.api.remember(f.event,f.sender,'https://chatgpt.com/');
  await f.api.remember({...f.event,state:'done'},f.sender,url);
  await f.api.remember({...f.event,state:'idle',reason:'cancelled'},f.sender,'https://chatgpt.com/c/other');
  f.chrome.tabs.get=async()=>{throw Error('closed');};
  await f.api.open(f.command());assert.equal(f.calls[0][1].url,url);
});
test('expired, wrong-source, missing and ephemeral records never open an unrelated page',async()=>{
  const f=fixture();await f.api.remember(f.event,f.sender,'https://chatgpt.com/');
  f.chrome.tabs.get=async()=>{throw Error('closed');};
  assert.equal(await f.api.open(f.command()),'missing');
  assert.equal(await f.api.open({...f.command(),source_id:'claude-web'}),'missing');
  assert.equal(await f.api.open({...f.command(),locator_id:crypto.randomUUID()}),'missing');
  f.local.seeend_task_returns[f.event.task_id].at=0;
  assert.equal(await f.api.open(f.command()),'missing');assert.equal(f.calls.length,0);
});
test('URLs are restricted to source sites, drop irrelevant queries, and exclude private tabs',async()=>{
  const f=fixture();
  assert.equal(f.api.safeURL('https://chatgpt.com/c/one?private=secret',source),'https://chatgpt.com/c/one');
  for(const value of ['https://example.com/','javascript:alert(1)','https://user:pass@chatgpt.com/c/one'])
    assert.equal(f.api.safeURL(value,source),null);
  assert.equal(await f.api.remember(f.event,{...f.sender,tab:{id:4,incognito:true}},url),f.event);
  assert.equal(Object.keys(f.local).length,0);
});
test('manifest retains its existing narrow permissions',()=>{
  const manifest=require('../aiflame/integrations/chatgpt_web/extension/manifest.json');
  assert.deepEqual(manifest.permissions,['nativeMessaging','storage']);
  assert.equal(manifest.host_permissions,undefined);
});
test('expired queued actions cannot focus a tab, and minimized windows are restored',async()=>{
  const f=fixture();await f.api.remember(f.event,f.sender,url);
  assert.equal(await f.api.open({...f.command(),timeout_ms:0}),'failed');
  assert.equal(f.calls.length,0);
  f.chrome.windows.get=async()=>({state:'minimized'});
  assert.equal(await f.api.open(f.command()),'opened');
  assert.equal(f.calls[1][1],8);assert.equal(f.calls[1][2].state,'normal');
  assert.equal(f.calls[2][2].focused,true);
});
test('unavailable native host backs off and successful reconnection resets the delay',async()=>{
  const f=fixture(), timers=[], ports=[];
  f.chrome.runtime.connectNative=()=>{
    const p={onMessage:{addListener:fn=>p.message=fn},onDisconnect:{addListener:fn=>p.closed=fn},postMessage(){},disconnect(){p.closed();}};
    ports.push(p);return p;
  };
  const context={chrome:f.chrome,crypto,URL,Date,FlameProviders:Providers,
    setTimeout:(fn,delay)=>{timers.push({fn,delay});return timers.length;},clearTimeout:id=>{timers[id-1].cancelled=true;}};
  vm.runInNewContext(script,context);
  const api=context.FlameReturns;
  api.ensure();await new Promise(setImmediate);
  ports[0].disconnect();assert.equal(timers[0].delay,2000);
  timers[0].fn();await new Promise(setImmediate);
  ports[1].disconnect();assert.equal(timers[1].delay,4000);
  api.ensure(true);await new Promise(setImmediate);
  assert.equal(timers[1].cancelled,true);
  ports[2].message({kind:'return-ready',ok:true});ports[2].disconnect();
  assert.equal(timers[2].delay,2000);
});
