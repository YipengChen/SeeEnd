const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {FlameTurn} = require('../aiflame/integrations/chatgpt_web/extension/state.js');

function page(send) {
  let now = 100000, next = 0, reply = 0, tick, receiver;
  const listeners = {}, sent = [];
  const snapshot = {route:'/c/fixture', final:null, turnKey:null, turnKeys:[],
    busy:false, online:true, ready:true, recognized:true};
  class Element {querySelector() {return {};}}
  const context = {
    FlameTurn,
    FlameProviders:{siteFor:()=>({id:'chatgpt-web'}),routePolicy:()=>({sourceId:'chatgpt-web'}),
      createAdapter:()=>({snapshot:()=>snapshot,isComposer:()=>true,isSubmit:()=>true,isStop:()=>false})},
    location:{origin:'https://chatgpt.com'}, navigator:{onLine:true}, Element,
    document:{addEventListener:(key,fn)=>listeners[key]=fn,documentElement:{},dispatchEvent(){}},
    window:{addEventListener(){}}, MutationObserver:class {observe(){}},
    crypto:{randomUUID:()=>String(++next)}, Date:class extends Date {static now() {return now;}},
    setInterval:fn=>tick=fn,setTimeout:()=>0,
    chrome:{runtime:{sendMessage:async message=>{sent.push(message.event);return send(message.event);},
      onMessage:{addListener:fn=>receiver=fn}}},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,
    '../aiflame/integrations/chatgpt_web/extension/content.js'),'utf8'),context);
  return {
    sent, tick:()=>tick(), flush:()=>new Promise(setImmediate), gap:()=>snapshot.busy=false,
    inspect() {let result;receiver({kind:'inspect'},null,value=>result=value);return result;},
    begin(kind='keydown') {
      listeners[kind]({isTrusted:true,key:'Enter',target:new Element()});
      snapshot.busy=true;snapshot.turnKey='reply-'+(++reply);
      snapshot.turnKeys=[...snapshot.turnKeys,snapshot.turnKey];snapshot.final=null;tick();
    },
    finish() {snapshot.busy=false;snapshot.final=snapshot.turnKey+':1';tick();now+=801;tick();},
  };
}

for (const kind of ['keydown','click','submit']) {
  for (const reject of [false,true]) {
    test(`${kind}: next submission reconnects after ${reject?'rejected':'failed'} delivery without replay`,async()=>{
      let available=false;
      const p=page(()=>{if(!available&&reject)throw Error('bridge unavailable');return {ok:available};});
      p.begin(kind);await p.flush();
      assert.equal(p.inspect().disabled,true);
      p.finish();available=true;p.tick();await p.flush();
      assert.deepEqual(p.sent.map(event=>event.state),['working'],'recovery alone cannot replay the failed run');
      p.begin(kind);p.finish();await p.flush();
      assert.deepEqual(p.sent.map(event=>event.state),['working','working','done']);
      assert.notEqual(p.sent[0].task_id,p.sent[1].task_id);
      assert.equal(p.sent[1].task_id,p.sent[2].task_id);
      assert.equal(p.inspect().disabled,false);
    });
  }
}

test('late failed start suppresses queued completion without cancelling a newer submission',async()=>{
  let release;
  const first=new Promise(resolve=>release=resolve);
  const p=page(()=>p.sent.length===1?first:{ok:true});
  p.begin();p.finish();await p.flush();
  assert.equal(p.sent.length,1,'first delivery remains in flight');
  p.begin();release({ok:false});await p.flush();
  assert.equal(p.inspect().disabled,false);
  assert.equal(p.inspect().active,true,'old failure cannot cancel the new live task');
  p.finish();await p.flush();
  assert.deepEqual(p.sent.map(event=>event.state),['working','working','done']);
  assert.notEqual(p.sent[0].task_id,p.sent[1].task_id);
  assert.equal(p.sent[1].task_id,p.sent[2].task_id);
});

test('repeated unavailable submissions are discarded independently before a fresh success',async()=>{
  let available=false;
  const p=page(()=>({ok:available}));
  p.begin();await p.flush();p.finish();
  p.begin();await p.flush();p.finish();
  available=true;p.begin();p.finish();await p.flush();
  assert.deepEqual(p.sent.map(event=>event.state),['working','working','working','done']);
  assert.equal(new Set(p.sent.slice(0,3).map(event=>event.task_id)).size,3);
});

test('failed supersession cancellation cannot disable its replacement task',async()=>{
  const p=page(event=>({ok:event.reason!=='cancelled'}));
  p.begin();await p.flush();
  p.gap();p.begin();await p.flush();
  assert.equal(p.inspect().disabled,false);
  assert.equal(p.inspect().active,true);
  p.finish();await p.flush();
  assert.deepEqual(p.sent.map(event=>event.state),['working','idle','working','done']);
  assert.equal(p.sent[0].task_id,p.sent[1].task_id);
  assert.equal(p.sent[2].task_id,p.sent[3].task_id);
});
