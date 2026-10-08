const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname,'../aiflame/integrations/chatgpt_web/extension');
function frames(hostname) {
  const native=new Map(), timers=new Map(), repeating=new Map(), listeners={}, errors=[];
  let id=0, time=0;
  const window={location:{hostname},requestAnimationFrame:fn=>{native.set(++id,fn);return id;},cancelAnimationFrame:key=>native.delete(key),
    setTimeout:fn=>{timers.set(++id,fn);return id;},
    setInterval:fn=>{repeating.set(++id,fn);return id;},clearInterval:key=>{repeating.delete(key);timers.delete(key);},clearTimeout:key=>{repeating.delete(key);timers.delete(key);}};
  const document={hidden:false,addEventListener:(kind,fn)=>listeners[kind]=fn,removeEventListener:(kind,fn)=>{if(listeners[kind]===fn)delete listeners[kind];}};
  Object.defineProperty(document,"visibilityState",{configurable:true,get:()=>document.hidden?"hidden":"visible"});
  vm.runInNewContext(fs.readFileSync(path.join(root,'frames.js'),'utf8'),{window,document,performance:{now:()=>time},Date:{now:()=>time},
    setTimeout:fn=>{timers.set(++id,fn);return id;},clearTimeout:key=>timers.delete(key)});
  return {window,document,native,timers,repeating,errors,advance:ms=>time+=ms,
    event:(kind,detail)=>listeners[kind]({detail}),
    nativeFrame(){for(const [key,fn] of [...native]){native.delete(key);fn(time);}}};
}
test('foreground animation frames retain timestamp, receiver, cancellation and single delivery',async()=>{
  const f=frames(),seen=[];
  f.window.requestAnimationFrame(function(t){seen.push([this===f.window,t]);});
  const cancelled=f.window.requestAnimationFrame(()=>seen.push('cancelled'));f.window.cancelAnimationFrame(cancelled);
  f.advance(42);f.nativeFrame();await f.event('aiflame:render-pulse');f.nativeFrame();
  assert.deepEqual(seen,[[true,42]]);assert.equal(f.timers.size,0);
});
test('hidden frames run only during a tracked task, cancellation and next-frame ordering survive',async()=>{
  const f=frames(),seen=[];f.document.hidden=true;
  f.window.requestAnimationFrame(()=>{seen.push('first');f.window.cancelAnimationFrame(cancelled);f.window.requestAnimationFrame(()=>seen.push('next'));});
  const cancelled=f.window.requestAnimationFrame(()=>seen.push('cancelled'));
  await f.event('aiflame:render-pulse');assert.deepEqual(seen,[]);
  f.event('aiflame:render-active',true);await f.event('aiflame:render-pulse');assert.deepEqual(seen,['first']);
  await f.event('aiflame:render-pulse');assert.deepEqual(seen,['first','next']);f.nativeFrame();assert.equal(seen.length,2);
});
test('stopping or lease expiry restores hidden-tab frame suspension; visibility restores native frames',async()=>{
  const f=frames(),seen=[];f.document.hidden=true;f.event('aiflame:render-active',true);
  f.window.requestAnimationFrame(()=>seen.push('stop'));f.event('aiflame:render-active',false);
  assert.equal(f.timers.size,0);await f.event('aiflame:render-pulse');assert.deepEqual(seen,[]);
  f.event('aiflame:render-active',true);f.advance(45001);await f.event('aiflame:render-pulse');assert.deepEqual(seen,[]);
  f.document.hidden=false;f.event('visibilitychange');f.nativeFrame();assert.deepEqual(seen,['stop']);
});
test('frame support is installed before website code and scoped to Qianwen and Zhipu',async()=>{
  const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json')));
  const main=manifest.content_scripts.find(script=>script.world==='MAIN');
  assert.equal(main.run_at,'document_start');assert.deepEqual(main.js,['frames.js']);
  assert.ok(main.matches.every(url=>/qianwen|chatglm/.test(url)));assert.deepEqual(manifest.permissions,['nativeMessaging','storage']);
});
test('worker ticks active original documents, resumes stored tasks, tolerates failures and stops when idle',async()=>{
  const session={tab_9:{event:{state:'working'},documentId:'original'},other:{event:{state:'working'}}},calls=[],timers=[];
  const context={URL,Date,crypto:globalThis.crypto,FlameProviders:require(path.join(root,'providers.js')),importScripts(){},setTimeout:fn=>{timers.push(fn);return timers.length;},
    chrome:{runtime:{id:'extension',onMessage:{addListener(){}},getURL:()=>''},
      storage:{session:{get:async()=>({...session})}},tabs:{onRemoved:{addListener(){}},onUpdated:{addListener(){}},sendMessage:async(...args)=>{calls.push(args);throw Error('unreachable');}}}};
  vm.runInNewContext(fs.readFileSync(path.join(root,'background.js'),'utf8'),context);await new Promise(setImmediate);
  assert.equal(calls.length,1);assert.equal(calls[0][0],9);assert.equal(calls[0][1].kind,'tick');assert.equal(calls[0][2].documentId,'original');
  assert.equal(timers.length,1);delete session.tab_9;await timers.shift()();assert.equal(timers.length,0);assert.equal(calls.length,1);
});
test('worker removes inactive leases without inventing events and cannot delete a newer task',async()=>{
  for (const replaced of [false,true]) {
    const original={event:{state:'working',source_id:'qianwen-web',task_id:'one',seq:1},documentId:'first'};
    const session={tab_9:original},removed=[],timers=[];
    const context={URL,Date,crypto:globalThis.crypto,FlameProviders:require(path.join(root,'providers.js')),importScripts(){},setTimeout:fn=>{timers.push(fn);return timers.length;},
      chrome:{runtime:{id:'extension',onMessage:{addListener(){}},getURL:()=>'',sendNativeMessage(){assert.fail('cannot invent terminal events');}},
        storage:{session:{get:async key=>key?{[key]:session[key]}:{...session},remove:async key=>{removed.push(key);delete session[key];}}},
        tabs:{onRemoved:{addListener(){}},onUpdated:{addListener(){}},sendMessage:async()=>{
          if(replaced)session.tab_9={...original,event:{...original.event,task_id:'two'}};
          return {source_id:'qianwen-web',active:false};
        }}}};
    vm.runInNewContext(fs.readFileSync(path.join(root,'background.js'),'utf8'),context);await new Promise(setImmediate);
    assert.equal(removed.length,replaced?0:1);
    if(!replaced){await timers.shift()();assert.equal(timers.length,0);}
  }
});
test('short rendering intervals retain native frequency and catch up when hidden timers are throttled',async()=>{
  const f=frames();let calls=0,received;
  const id=f.window.setInterval(function(a){calls++;received=[this===f.window,a];},20,'opaque');
  f.advance(20);f.repeating.get(id)();assert.equal(calls,1);assert.deepEqual(received,[true,'opaque']);
  f.document.hidden=true;f.event('aiflame:render-active',true);
  f.advance(5000);await f.event('aiflame:render-pulse');assert.equal(calls,251);
  f.advance(5*60000);f.event('aiflame:render-active',true);await f.event('aiflame:render-pulse');assert.equal(calls,501,'bounded batch after intensive timer throttling');
  f.window.clearTimeout(id);await f.event('aiflame:render-pulse');assert.equal(calls,501);assert.equal(f.repeating.size,0);
});
test('inactive intervals never catch up, long intervals stay native and callbacks can cancel themselves',async()=>{
  const f=frames();let short=0,long=0,cancelled=0;
  const id=f.window.setInterval(()=>short++,20);f.window.setInterval(()=>long++,1000);
  f.document.hidden=true;f.advance(5000);f.repeating.get(id)();assert.equal(short,1);assert.equal(long,0);
  f.event('aiflame:render-active',true);
  const self=f.window.setInterval(()=>{cancelled++;f.window.clearInterval(self);},20);
  f.advance(1000);await f.event('aiflame:render-pulse');assert.equal(short,51);assert.equal(cancelled,1);assert.equal(long,0);
  f.event('aiflame:render-active',false);f.advance(10000);f.repeating.get(id)();assert.equal(short,52);
});
test('awaited animation-frame chains catch up in bounded batches and stop on cancellation',async()=>{
  const f=frames();f.document.hidden=true;f.event('aiflame:render-active',true);
  let steps=0;
  async function type(){while(steps<500){await new Promise(f.window.requestAnimationFrame);steps++;}}
  type();f.advance(1000);await f.event('aiflame:render-pulse');assert.ok(steps>=50&&steps<=60);
  f.advance(1000);await f.event('aiflame:render-pulse');assert.ok(steps>=100&&steps<=120);
  f.event('aiflame:render-active',false);const stopped=steps;f.advance(1000);await f.event('aiflame:render-pulse');assert.equal(steps,stopped);
});
test('Zhipu resumes only its published renderer, restores visibility before microtasks and never invents completion',async()=>{
  const f=frames('chatglm.cn'),document=f.document;f.document.hidden=true;
  let ready=false,completed=false,unrelated=0,after,resumes=0;
  function renderer(){if(document.visibilityState==='visible'){
    const answerListDisplay=ready,answerListOutput=answerListDisplay;
    completed=answerListOutput;resumes++;Promise.resolve().then(()=>after=document.visibilityState);
  }}
  document.addEventListener('visibilitychange',renderer);
  document.addEventListener('visibilitychange',()=>unrelated++);
  await f.event('aiflame:render-pulse');assert.equal(completed,false);
  f.event('aiflame:render-active',true);f.advance(1000);await f.event('aiflame:render-pulse');assert.equal(completed,false);
  ready=true;f.advance(1000);await f.event('aiflame:render-pulse');assert.equal(completed,false);
  assert.equal(resumes,1,'never rewind an already resumed typewriter');
  f.event('aiflame:render-active',false);f.event('aiflame:render-active',true);await f.event('aiflame:render-pulse');assert.equal(completed,true);
  assert.equal(document.visibilityState,'hidden');assert.equal(after,'hidden');assert.equal(unrelated,0);
  document.removeEventListener('visibilitychange',renderer);completed=false;await f.event('aiflame:render-pulse');assert.equal(completed,false);
});
test('short timeouts wake only once after their deadline; cancellation and long delays remain native',async()=>{
  const f=frames();f.document.hidden=true;f.event('aiflame:render-active',true);let short=0,long=0,cancelled=0;
  const id=f.window.setTimeout(()=>short++,30);f.window.setTimeout(()=>long++,1000);
  const other=f.window.setTimeout(()=>cancelled++,30);f.window.clearInterval(other);
  f.advance(29);await f.event('aiflame:render-pulse');assert.equal(short,0);
  f.advance(1);await f.event('aiflame:render-pulse');assert.equal(short,1);assert.equal(f.timers.has(id),false);
  f.advance(1000);await f.event('aiflame:render-pulse');assert.equal(short,1);assert.equal(long,0);assert.equal(cancelled,0);
  f.event('aiflame:render-active',false);f.window.setTimeout(()=>short++,0);await f.event('aiflame:render-pulse');assert.equal(short,1);
});
test('recursive short timers catch up elapsed deadlines without changing clocks or running future callbacks',async()=>{
  const f=frames();f.document.hidden=true;f.event('aiflame:render-active',true);let calls=0;
  function print(){calls++;if(calls<500)f.window.setTimeout(print,30);}
  f.window.setTimeout(print,30);f.advance(1000);await f.event('aiflame:render-pulse');
  assert.equal(calls,33,'one second advances original 30 ms deadlines');
  await f.event('aiflame:render-pulse');assert.equal(calls,33,'nothing executes before its next deadline');
  f.advance(1000);await f.event('aiflame:render-pulse');assert.equal(calls,66);
  f.event('aiflame:render-active',false);f.advance(1000);await f.event('aiflame:render-pulse');assert.equal(calls,66);
});
test('Zhipu renderer flush leaves enough time for recursive final steps instead of resetting them each pulse',async()=>{
  const f=frames('chatglm.cn'),document=f.document;f.document.hidden=true;f.event('aiflame:render-active',true);
  let index=0,completed=false;
  function renderer(){if(document.visibilityState==='visible'){
    const answerListDisplay=true,answerListOutput=answerListDisplay;index=answerListOutput?0:-1;
  }}
  function print(){if(index===1){completed=true;return;}index++;f.window.setTimeout(print,30);}
  document.addEventListener('visibilitychange',renderer);f.window.setTimeout(print,30);
  f.advance(30);await f.event('aiflame:render-pulse');assert.equal(index,1);
  f.advance(30);await f.event('aiflame:render-pulse');assert.equal(completed,true);
});
