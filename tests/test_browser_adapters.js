const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const {FlameTurn} = require('../aiflame/integrations/chatgpt_web/extension/state.js');
const {SITES, siteFor, createAdapter, routePolicy, routeFor} = require('../aiflame/integrations/chatgpt_web/extension/providers.js');
// Reduced structural fixtures from public frontend bundles inspected 2026-09-30.
// They contain no private messages; selectors intentionally include icon-only and disabled controls.
const fixtures = {
  // New adapters: structural contract fixtures, not authenticated live-site captures.
  'claude-web': {home:'/new', chat:'/chat/test', composer:'<div class="ProseMirror" contenteditable="true"></div>',
    stop:'<button aria-label="Stop response"></button>',
    answer:'<div data-testid="assistant-message"><div data-is-streaming="false"></div><button aria-label="Copy"></button><button aria-label="Good response"></button></div>'},
  'gemini-web': {home:'/app', chat:'/app/test', composer:'<rich-textarea><div class="ql-editor" contenteditable="true"></div></rich-textarea>',
    stop:'<button class="stop-button"></button>',
    answer:'<model-response><button data-test-id="copy-button"></button><button data-test-id="thumb-up-button"></button></model-response>'},
  'chatgpt-web': {home:'/', chat:'/c/test', composer:'<main><textarea id="prompt-textarea"></textarea></main>',
    stop:'<button data-testid="stop-button"></button>',
    answer:'<article><div data-message-author-role="assistant"></div><button data-testid="copy-turn-action-button"></button><button data-testid="good-response-turn-action-button"></button></article>'},
  'deepseek-web': {home:'/', chat:'/a/chat/s/test', composer:'<textarea id="chat-input" placeholder="给 DeepSeek 发送消息"></textarea>',
    stop:'<div class="ds-button _52c986b"><svg><path d="M2 4.88C2 3.68009 2 3.08013"></path></svg></div>',
    answer:'<div class="_4f9bf79"><div class="ds-message"><div class="ds-assistant-message-main-content"></div></div><div class="_965abe9"><div class="ds-button"></div><div class="ds-button"></div><div class="ds-button"></div><div class="ds-button"></div></div></div>'},
  'qianwen-web': {home:'/', chat:'/chat/test', composer:'<div data-chat-input-shell="true"><div role="textbox" data-slate-editor="true" contenteditable="true"><span data-slate-leaf="true"></span></div></div>',
    stop:'<button aria-label="停止回答"></button>',
    answer:'<div class="chat-answers-card-wrap" data-chat-answers-wrap="opaque"><div class="answerItem"></div><div data-answer-feedback-toolbar><div><svg><path d="M832 64a96 96 0 0 1 96 96v480"></path></svg></div><div><svg><path d="M2.5 14.049h8.77q1.002 0 1.766-.647"></path></svg></div></div></div>'},
  'doubao-web': {home:'/chat/', chat:'/chat/test', composer:'<textarea data-testid="chat_input_input"></textarea>',
    stop:'<div data-testid="chat_input_local_break_button"></div>',
    answer:'<section class="current-message"><div class="action-row"><button data-testid="message_action_copy"></button><button data-testid="message_action_regenerate"></button></div></section>'},
  'yuanbao-web': {home:'/chat/agent', chat:'/chat/agent/test', composer:'<div class="chat-input-editor"><div class="ql-editor" contenteditable="true"></div></div>',
    stop:'<div id="yuanbao-send-btn" aria-label="停止回答"><img src="stop-chat-icon-light.png"></div>',
    answer:'<div class="agent-chat__list__item--ai"><div class="agent-chat__bubble--ai"></div><div class="agent-chat__toolbar"><div aria-label="复制" class="ToolbarCopy_icon__5Odjl"></div><div aria-label="点赞"></div></div></div>'},
  'kimi-web': {home:'/', chat:'/chat/test', composer:'<div class="chat-input-editor"><div contenteditable="true"></div></div>',
    stop:'<div class="send-button-container stop"></div>',
    answer:'<div class="segment segment-assistant"><div class="segment-assistant-actions-content"><div class="icon-button"></div><div class="icon-button"></div><div class="icon-button"></div></div></div>'},
  'zhipu-web': {home:'/main/alltoolsdetail', chat:'/main/detail?cid=test', composer:'<div class="prosemirror-editor"><div contenteditable="true"></div></div>',
    stop:'<button class="btn stop" ga-data="stop"></button>',
    answer:'<div class="glm-3-conversation"><div class="answer"><div class="interact-operate"><i class="shim copy canuse"></i><i class="shim thumbs-up canuse"></i><i class="shim thumbs-down canuse"></i></div></div></div>'},
};
function setup(site) {
  const f = fixtures[site.id];
  const dom = new JSDOM(f.composer + '<div id="messages"></div><div id="busy"></div>', {url:site.origins[0]+f.home});
  const doc = dom.window.document;
  const visible = el => !!el && el.isConnected && !el.closest('[hidden],[style="display:none"]');
  const adapter = createAdapter(site, doc, dom.window.location, () => true, visible);
  let now = 100000, next = 0, answerId = 0;
  const events = [];
  const machine = new FlameTurn(e => events.push(e), () => String(++next), () => now, routePolicy(site));
  return {f, dom, doc, adapter, machine, events, advance:ms=>now+=ms,
    answer() { doc.querySelector('#messages').insertAdjacentHTML('beforeend',f.answer.replace('data-chat-answers-wrap="opaque"','data-chat-answers-wrap="opaque-' + (++answerId) + '"')); },
    generating() { doc.querySelector('#busy').innerHTML=f.stop; },
    finish() { doc.querySelector('#busy').replaceChildren(); },
    check() { machine.update(adapter.snapshot()); }};
}
for (const site of SITES) {
  test(site.name + ': a tracked incomplete reply renews through loading-marker gaps during long output', () => {
    const f=setup(site);
    f.machine.arm(f.adapter.snapshot()); f.generating(); f.check(); f.answer();
    f.doc.querySelector('#messages').setAttribute('aria-disabled','true'); f.check(); f.finish();
    for (let i=0;i<24;i++) { f.advance(30000); f.check(); }
    assert.equal(f.events.filter(e=>e.state==='working').length,25);
    assert.ok(f.events.every(e=>e.state==='working'));
    f.doc.querySelector('#messages').removeAttribute('aria-disabled'); f.check(); f.advance(801); f.check();
    assert.equal(f.events.at(-1).state,'done'); f.dom.window.close();
  });
  test(site.name + ': new conversation, working and explicit completion use independent source', () => {
    const f = setup(site);
    assert.equal(f.adapter.snapshot().recognized,true);
    f.answer(); f.check(); assert.equal(f.events.length,0,'history is quiet');
    f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
    f.dom.reconfigure({url:site.origins[0]+f.f.chat}); f.check();
    assert.equal(f.events.length,1); assert.equal(f.events[0].source_id,site.id);
    f.answer(); f.finish(); f.check(); f.advance(801); f.check(); f.check();
    assert.deepEqual(f.events.map(e=>e.state),['working','done']);
    assert.equal(f.events[0].task_id,f.events[1].task_id);
    f.dom.window.close();
  });
  test(site.name + ': stale final controls, stop and route switches never finish', () => {
    const f = setup(site); f.answer();
    f.machine.arm(f.adapter.snapshot()); f.generating(); f.check(); f.finish(); f.check(); f.advance(1000); f.check();
    assert.deepEqual(f.events.map(e=>e.state),['working']);
    f.machine.cancel(); f.answer(); f.advance(1000); f.check();
    assert.deepEqual(f.events.map(e=>e.state),['working','idle']);
    f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
    f.dom.reconfigure({url:site.origins[0]+f.f.chat}); f.check();
    f.dom.reconfigure({url:site.origins[0]+f.f.chat.replace('test','other')}); f.check();
    assert.equal(f.events.at(-1).reason,'cancelled'); f.dom.window.close();
  });
  test(site.name + ': hidden generation controls and disabled final controls cannot signal success', () => {
    const f = setup(site); f.generating();
    f.doc.querySelector('#busy').hidden=true;
    assert.equal(f.adapter.snapshot().busy,false);
    f.machine.arm(f.adapter.snapshot()); f.check(); assert.equal(f.events.length,0);
    f.doc.querySelector('#busy').hidden=false; f.check(); f.answer(); f.finish();
    f.doc.querySelector('#messages').setAttribute('aria-disabled','true'); f.check(); f.advance(1000); f.check();
    assert.deepEqual(f.events.map(e=>e.state),['working']); f.dom.window.close();
  });
  test(site.name + ': regenerated responses can reuse the same DOM controls', () => {
    const f = setup(site); f.answer();
    f.machine.arm(f.adapter.snapshot()); f.generating();
    f.doc.querySelector('#messages').setAttribute('aria-disabled','true'); f.check();
    f.doc.querySelector('#messages').removeAttribute('aria-disabled'); f.finish(); f.check(); f.advance(801); f.check();
    assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
  });
  test(site.name + ': inspection and emitted events never read conversation text', () => {
    const f = setup(site);
    for (const prototype of [f.dom.window.Element.prototype, f.dom.window.Node.prototype]) {
      for (const field of ['textContent','innerText']) Object.defineProperty(prototype,field,{get(){throw Error('private text access');},configurable:true});
    }
    Object.defineProperty(f.dom.window.HTMLTextAreaElement.prototype,'value',{get(){throw Error('private prompt access');},configurable:true});
    f.machine.arm(f.adapter.snapshot()); f.generating(); f.check(); f.answer(); f.finish(); f.check(); f.advance(801); f.check();
    f.adapter.inspectBusyControls(); f.adapter.inspectReplyControls(); f.adapter.inspectReplyLayout(); f.adapter.inspectReplyAncestors(); f.adapter.inspectPageControls(); f.adapter.inspectComposerControls();
    assert.deepEqual(f.events.map(e=>e.state),['working','done']);
    assert.ok(f.events.every(e=>Object.keys(e).every(k=>['schema_version','event_id','source_id','task_id','seq','state','occurred_at','ttl_ms','reason'].includes(k))));
    f.dom.window.close();
  });
  test(site.name + ': long output and response/toolbar remounts share one lifecycle', () => {
    const f=setup(site); f.answer();
    f.doc.querySelector('#messages').lastElementChild.setAttribute('data-message-id','history');
    f.machine.arm(f.adapter.snapshot()); f.generating(); f.answer();
    let reply=f.doc.querySelector('#messages').lastElementChild;
    reply.setAttribute('data-message-id','current-reply');
    const body=f.doc.createElement('pre'); body.append(f.doc.createTextNode('synthetic long response\n'.repeat(5000))); reply.prepend(body);
    f.check();
    for (let i=0;i<24;i++) {
      f.advance(30000); const replacement=reply.cloneNode(true); reply.replaceWith(replacement); reply=replacement; f.check();
      assert.ok(f.machine.active); assert.equal(f.events.at(-1).state,'working');
    }
    assert.equal(f.events.length,25,'twelve minutes renew the same task');
    assert.equal(new Set(f.events.map(e=>e.task_id)).size,1);
    f.finish(); f.check();
    for (const elapsed of [400,401]) {
      f.advance(elapsed); const replacement=reply.cloneNode(true); reply.replaceWith(replacement); reply=replacement; f.check();
    }
    assert.equal(f.events.at(-1).state,'done'); assert.equal(f.events.filter(e=>e.state==='done').length,1);
    f.check(); assert.equal(f.events.filter(e=>e.state==='done').length,1); f.dom.window.close();
  });
  test(site.name + ': a temporarily unrendered newest reply cannot fall back to history', () => {
    const f=setup(site); f.answer();
    f.doc.querySelector('#messages').lastElementChild.setAttribute('data-message-id','history');
    f.machine.arm(f.adapter.snapshot()); f.generating(); f.answer();
    const reply=f.doc.querySelector('#messages').lastElementChild; reply.setAttribute('data-message-id','current-reply'); f.check(); f.finish();
    reply.hidden=true; f.check(); f.advance(1000); f.check();
    assert.deepEqual(f.events.map(e=>e.state),['working']);
    // Virtualization may remove the newest reply and leave a history row behind.
    reply.remove(); f.check(); f.advance(1000); f.check();
    assert.deepEqual(f.events.map(e=>e.state),['working']);
    reply.hidden=false; f.doc.querySelector('#messages').append(reply); f.check(); f.advance(801); f.check();
    assert.equal(f.events.at(-1).state,'done'); f.dom.window.close();
  });
  test(site.name + ': rendered controls outside the viewport still finish without scrolling', () => {
    const f=setup(site);
    f.dom.window.Element.prototype.getClientRects=function(){return this.isConnected&&!this.closest('[hidden]')?[{x:0,y:30000,width:20,height:20}]:[];};
    f.adapter=createAdapter(site,f.doc,f.dom.window.location,()=>true);
    f.machine.arm(f.adapter.snapshot()); f.generating(); f.check(); f.answer(); f.finish(); f.check(); f.advance(801); f.check();
    assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
  });
  test(site.name + ': long-output cancellation cannot be followed by a late success', () => {
    const f=setup(site); f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
    for (let i=0;i<12;i++) {f.advance(30000);f.check();}
    f.machine.cancel(); f.answer(); f.finish(); f.check(); f.advance(1000); f.check();
    assert.equal(f.events.at(-1).reason,'cancelled'); assert.equal(f.events.some(e=>e.state==='done'),false); f.dom.window.close();
  });
}
test('query-based conversation IDs are tracked, unrelated query parameters are ignored', () => {
  const a = new URL('https://chatglm.cn/main/detail?cid=one&lang=zh');
  const b = new URL('https://chatglm.cn/main/detail?cid=two&lang=zh');
  assert.notEqual(routeFor(a),routeFor(b));
  assert.equal(routeFor(a),routeFor(new URL('https://chatglm.cn/main/detail?lang=en&cid=one')));
});
test('exact HTTPS origins are enforced and manifest permissions match providers', () => {
  const manifest = require('../aiflame/integrations/chatgpt_web/extension/manifest.json');
  assert.equal(manifest.version,require('../aiflame/integrations/chatgpt_web/extension/providers.js').VERSION);
  for (const site of SITES) for (const origin of site.origins) {
    assert.equal(siteFor(origin)?.id,site.id);
    assert.ok(manifest.content_scripts[0].matches.some(match=>match.startsWith(origin+'/')));
    assert.equal(siteFor(origin.replace('https:','http:')),null);
    assert.equal(siteFor(origin.replace('https://','https://evil.')),null);
    assert.equal(siteFor(origin+'.evil.test'),null);
  }
  assert.equal(siteFor('https://evil.test/chat.deepseek.com'),null);
});
test('Chrome manifest loads the shared detector without Node require or module globals', () => {
  const root=path.join(__dirname,'../aiflame/integrations/chatgpt_web/extension');
  const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json')));
  const context=vm.createContext({URL,URLSearchParams});
  for (const script of manifest.content_scripts[0].js.filter(s=>s!=='content.js')) {
    vm.runInContext(fs.readFileSync(path.join(root,script),'utf8'),context);
  }
  for (const site of SITES) {
    const f=setup(site), adapter=context.FlameProviders.createAdapter(context.FlameProviders.siteFor(site.origins[0]),f.doc,f.dom.window.location,()=>true,el=>!!el&&el.isConnected);
    assert.equal(adapter.snapshot().recognized,true); f.dom.window.close();
  }
});
test('background binds each event to its actual website and rejects impersonation', async () => {
  let handler; const nativeEvents=[];
  const context = {URL, crypto:globalThis.crypto, Date, setTimeout:()=>1, importScripts(){}, FlameProviders:require('../aiflame/integrations/chatgpt_web/extension/providers.js'),
    chrome:{runtime:{id:'extension',getURL:f=>'chrome-extension://extension/'+f,
      onMessage:{addListener:fn=>handler=fn},sendNativeMessage:(host,event,cb)=>{nativeEvents.push(event);cb({ok:true});}},
      storage:{local:{set:async()=>{}},session:{set:async()=>{},get:async()=>({}),remove:async()=>{}}},
      tabs:{onRemoved:{addListener(){}},onUpdated:{addListener(){}}}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../aiflame/integrations/chatgpt_web/extension/background.js'),'utf8'),context);
  for (const site of SITES) {
    const event={schema_version:'1.0',event_id:crypto.randomUUID(),source_id:site.id,task_id:crypto.randomUUID(),seq:1,state:'working',occurred_at:new Date().toISOString(),ttl_ms:60000};
    const sender={id:'extension',frameId:0,url:site.origins[0]+'/chat/test',tab:{id:1}};
    await new Promise(resolve=>assert.equal(handler({kind:'event',event},sender,resolve),true));
    assert.equal(handler({kind:'event',event:{...event,source_id:'codex'}},sender,()=>{}),undefined);
    assert.equal(handler({kind:'event',event:{...event,source_id:SITES.find(s=>s.id!==site.id).id}},sender,()=>{}),undefined);
    assert.equal(handler({kind:'event',event:{...event,prompt:'private'}},sender,()=>{}),undefined);
    assert.equal(handler({kind:'event',event},{...sender,frameId:1},()=>{}),undefined);
    assert.equal(handler({kind:'event',event},{...sender,url:'https://evil.test'},()=>{}),undefined);
  }
  assert.equal(nativeEvents.length,SITES.length);
});

test('Kimi provisional conversation route becomes permanent without cancelling', () => {
  const site=SITES.find(s=>s.id==='kimi-web'), f=setup(site);
  f.machine.arm(f.adapter.snapshot()); f.generating();
  f.dom.reconfigure({url:site.origins[0]+'/chat/p0000000-0000-4000-8000-000000000001'}); f.check();
  f.dom.reconfigure({url:site.origins[0]+'/chat/permanent'}); f.check();
  f.answer(); f.finish(); f.check(); f.advance(801); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
});
test('Doubao new-chat first reply survives home, local temporary ID and permanent ID', () => {
  const site=SITES.find(s=>s.id==='doubao-web'), f=setup(site);
  f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
  f.dom.reconfigure({url:site.origins[0]+'/chat/local_1827222924710319'}); f.check();
  assert.ok(f.machine.active);
  f.dom.reconfigure({url:site.origins[0]+'/chat/permanent'}); f.check();
  assert.ok(f.machine.active,'committing a temporary conversation is not a user switch');
  f.answer(); f.finish(); f.check(); f.advance(801); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
});
test('Doubao submission on a local URL follows one permanent ID, later switches cancel', () => {
  const site=SITES.find(s=>s.id==='doubao-web'), f=setup(site);
  f.dom.reconfigure({url:site.origins[0]+'/chat/local_1827222924710319'});
  f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
  f.dom.reconfigure({url:site.origins[0]+'/chat/permanent'}); f.check();
  assert.ok(f.machine.active);
  f.dom.reconfigure({url:site.origins[0]+'/chat/other'}); f.check();
  f.answer(); f.finish(); f.check(); f.advance(801); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working','idle']);
  assert.equal(f.machine.lastTransition.cause,'navigation'); f.dom.window.close();
});
test('Doubao creation keeps the outer target item across reply remounts and server-ID changes', () => {
  const site=SITES.find(s=>s.id==='doubao-web'),f=setup(site);
  f.machine.arm(f.adapter.snapshot());f.generating();
  f.doc.querySelector('#messages').innerHTML='<div data-target-id="local-reply"><div data-testid="receive_message"></div></div>';f.check();
  const key=f.adapter.snapshot().turnKey;
  f.dom.reconfigure({url:site.origins[0]+'/chat/local_1827222924710319'});f.check();
  const item=f.doc.querySelector('[data-target-id]');item.setAttribute('data-target-id','server-reply');
  item.innerHTML='<div data-testid="receive_message"></div>';f.check();
  assert.equal(f.adapter.snapshot().turnKey,key);
  f.dom.reconfigure({url:site.origins[0]+'/chat/permanent'});f.check();
  item.outerHTML='<div data-target-id="server-reply"><div data-testid="receive_message"><div data-testid="suggest_message_list"><div data-testid="suggest_message_item"></div></div></div></div>';
  f.finish();f.check();f.advance(801);f.check();
  assert.equal(f.adapter.snapshot().turnKey,key);
  assert.deepEqual(f.events.map(e=>e.state),['working','done']);f.dom.window.close();
});
test('Doubao target containers keep historical follow-up controls out of the current reply', () => {
  const f=setup(SITES.find(s=>s.id==='doubao-web'));
  f.doc.querySelector('#messages').innerHTML='<div data-target-id="history"><div data-testid="receive_message"><div data-testid="suggest_message_list"><div data-testid="suggest_message_item"></div></div></div></div>';
  f.machine.arm(f.adapter.snapshot());f.generating();
  f.doc.querySelector('#messages').insertAdjacentHTML('beforeend','<div data-target-id="current"><div data-testid="receive_message"></div></div>');f.check();f.finish();f.check();f.advance(801);f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working']);f.dom.window.close();
});
test('Zhipu agent new conversation accepts its first cid, later cid changes cancel', () => {
  const site=SITES.find(s=>s.id==='zhipu-web'), f=setup(site);
  f.dom.reconfigure({url:site.origins[0]+'/main/gdetail/assistant'});
  f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
  f.dom.reconfigure({url:site.origins[0]+'/main/gdetail/assistant?cid=first'}); f.check();
  assert.equal(f.events.length,1);
  f.dom.reconfigure({url:site.origins[0]+'/main/gdetail/assistant?cid=other'}); f.check();
  assert.equal(f.events.at(-1).reason,'cancelled'); f.dom.window.close();
});
test('Doubao cannot combine controls from separate responses', () => {
  const site=SITES.find(s=>s.id==='doubao-web'), f=setup(site); f.answer();
  f.doc.querySelector('#messages').insertAdjacentHTML('beforeend','<section><button data-testid="message_action_copy"></button></section>');
  assert.equal(f.adapter.snapshot().final,null); f.dom.window.close();
});

test('DeepSeek final controls are scoped to the response wrapper, never a previous reply', () => {
  const f=setup(SITES.find(s=>s.id==='deepseek-web')); f.answer();
  assert.ok(f.adapter.snapshot().final);
  f.doc.querySelector('#messages').insertAdjacentHTML('beforeend','<div class="_4f9bf79"><div class="ds-message"><div class="ds-assistant-message-main-content"></div></div></div>');
  assert.equal(f.adapter.snapshot().final,null);
  f.dom.window.close();
});
test('ChatGPT custom GPT and project chats preserve the creation transition', () => {
  const site=SITES.find(s=>s.id==='chatgpt-web');
  for (const route of ['/g/g-example','/g/g-p-example/project']) {
    const f=setup(site); f.dom.reconfigure({url:site.origins[0]+route});
    f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
    f.dom.reconfigure({url:site.origins[0]+'/g/g-example/c/local-chatgpt:test'}); f.check();
    f.dom.reconfigure({url:site.origins[0]+'/g/g-example/c/permanent'}); f.check();
    f.answer(); f.finish(); f.check(); f.advance(801); f.check();
    assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
  }
});
test('ChatGPT identity remains stable when message IDs arrive or change during streaming', () => {
  const f=setup(SITES.find(s=>s.id==='chatgpt-web'));
  f.machine.arm(f.adapter.snapshot()); f.generating();
  f.doc.querySelector('#messages').innerHTML='<article><div data-message-author-role="assistant"></div></article>';
  f.check(); const generatingKey=f.adapter.snapshot().turnKey;
  const anchor=f.doc.querySelector('[data-message-author-role="assistant"]');
  anchor.setAttribute('data-message-id','temporary-client-id'); f.check();
  anchor.setAttribute('data-message-id','permanent-server-id'); f.check();
  f.doc.querySelector('article').insertAdjacentHTML('beforeend','<button data-testid="copy-turn-action-button"></button><button data-testid="good-response-turn-action-button"></button>');
  f.finish(); f.check(); f.advance(801); f.check();
  assert.equal(f.adapter.snapshot().turnKey,generatingKey);
  assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
});
test('ChatGPT late server ID survives response remount and still completes', () => {
  const f=setup(SITES.find(s=>s.id==='chatgpt-web'));
  f.machine.arm(f.adapter.snapshot()); f.generating();
  f.doc.querySelector('#messages').innerHTML='<article data-testid="conversation-turn-1"><div data-message-author-role="assistant"></div></article>';
  f.check(); const generatingKey=f.adapter.snapshot().turnKey;
  f.doc.querySelector('[data-message-author-role]').setAttribute('data-message-id','server-id'); f.check();
  f.doc.querySelector('#messages').innerHTML=f.f.answer.replace('data-message-author-role="assistant"','data-message-author-role="assistant" data-message-id="server-id"');
  f.finish(); f.check(); f.advance(801); f.check();
  assert.equal(f.adapter.snapshot().turnKey,generatingKey);
  assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
});
test('ChatGPT generic conversation-turn test IDs do not merge history with a new reply', () => {
  const f=setup(SITES.find(s=>s.id==='chatgpt-web'));
  f.answer(); f.doc.querySelector('article').setAttribute('data-testid','conversation-turn-message');
  const historyKey=f.adapter.snapshot().turnKey;
  f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
  f.answer(); f.doc.querySelectorAll('article')[1].setAttribute('data-testid','conversation-turn-message');
  f.check(); assert.notEqual(f.adapter.snapshot().turnKey,historyKey);
  f.finish(); f.check(); f.advance(801); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
});
test('ChatGPT user action toolbar appearing before assistant cannot become the tracked reply', () => {
  const f=setup(SITES.find(s=>s.id==='chatgpt-web'));
  f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
  f.doc.querySelector('main').insertAdjacentHTML('beforeend','<article data-message-author-role="user"><div class="turn-action-controls"><button aria-label="复制消息"></button><button aria-label="编辑消息"></button></div></article>');
  f.check(); assert.equal(f.adapter.snapshot().turnKey,null);
  f.answer(); f.check(); f.finish(); f.check(); f.advance(801); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
});
test('ChatGPT redesigned UI without author-role nodes completes the assistant toolbar only', () => {
  const f=setup(SITES.find(s=>s.id==='chatgpt-web'));
  f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
  f.doc.querySelector('main').insertAdjacentHTML('beforeend','<div class="turn-action-controls"><button aria-label="复制消息"></button><button aria-label="编辑消息"></button></div>');
  f.check(); assert.equal(f.adapter.snapshot().turnKey,null);
  f.doc.querySelector('main').insertAdjacentHTML('beforeend','<div class="turn-action-controls"><button aria-label="复制"></button><button aria-label="重新生成回复"></button></div>');
  f.finish(); f.check(); f.advance(801); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
});
test('Doubao guest replies finish with new follow-up controls, never history or silence', () => {
  const f=setup(SITES.find(s=>s.id==='doubao-web'));
  const reply='<div data-testid="receive_message"><div data-testid="suggest_message_list"><div data-testid="suggest_message_item"></div></div></div>';
  f.doc.querySelector('#messages').innerHTML=reply;
  f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
  f.finish(); f.check(); f.advance(1000); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working']);
  f.generating(); f.doc.querySelector('#messages').insertAdjacentHTML('beforeend','<div data-testid="receive_message"></div>'); f.check();
  f.finish(); f.check(); f.advance(1000); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working']);
  f.doc.querySelector('[data-testid="receive_message"]:last-child').innerHTML='<div data-testid="suggest_message_list"><div data-testid="suggest_message_item"></div></div>';
  f.check(); f.advance(801); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
});
test('Zhipu current conversation-list markup and alltoolsdetail cid routing share the lifecycle', () => {
  const site=SITES.find(s=>s.id==='zhipu-web'), f=setup(site);
  f.doc.querySelector('#messages').className='conversation-list';
  f.dom.reconfigure({url:site.origins[0]+'/main/alltoolsdetail?lang=zh'});
  f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
  f.dom.reconfigure({url:site.origins[0]+'/main/alltoolsdetail?lang=zh&cid=current'}); f.check();
  f.doc.querySelector('#messages').innerHTML=f.f.answer.replace('<div class="glm-3-conversation">','<div class="conversation-item">');
  f.finish(); f.check(); f.advance(801); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
});
test('Zhipu loading marker starts only the latest answer, not an old answer', () => {
  const f=setup(SITES.find(s=>s.id==='zhipu-web'));
  f.doc.querySelector('#messages').className='conversation-list';
  f.doc.querySelector('#messages').innerHTML='<div class="answer"><div class="answer-loading"></div></div>';
  assert.equal(f.adapter.snapshot().busy,true);
  f.answer(); assert.equal(f.adapter.snapshot().busy,false);
  f.machine.arm(f.adapter.snapshot());
  f.doc.querySelector('#messages').insertAdjacentHTML('beforeend','<div class="answer"><div class="answer-loading"></div></div>'); f.check();
  assert.equal(f.events[0].state,'working'); f.dom.window.close();
});
test('Zhipu can replace its loading answer inside the same conversation item', () => {
  const f=setup(SITES.find(s=>s.id==='zhipu-web'));
  f.doc.querySelector('#messages').className='conversation-list';
  f.machine.arm(f.adapter.snapshot());
  f.doc.querySelector('#messages').innerHTML='<div class="conversation-item"><div class="answer"><div class="answer-loading"></div></div></div>'; f.check();
  const key=f.adapter.snapshot().turnKey;
  f.doc.querySelector('.conversation-item').innerHTML='<div class="answer"><div class="interact-operate"><i class="shim copy canuse"></i><i class="shim thumbs-up canuse"></i></div></div>';
  f.check(); f.advance(801); f.check();
  assert.equal(f.adapter.snapshot().turnKey,key);
  assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
});
test('Zhipu current enter.searching control preserves generation and captures an icon stop click', () => {
  const f=setup(SITES.find(s=>s.id==='zhipu-web'));
  f.doc.querySelector('#busy').innerHTML='<div class="enter is-main-chat"><div class="enter-icon-container"><img class="enter_icon"><span class="enter-icon-text"></span></div></div>';
  const target=f.doc.querySelector('.enter-icon-text');
  assert.equal(f.adapter.isSubmit(target),true); assert.equal(f.adapter.isStop(target),false);
  f.machine.arm(f.adapter.snapshot()); f.doc.querySelector('.enter').classList.add('searching'); f.check();
  assert.equal(f.adapter.snapshot().busy,true); assert.equal(f.adapter.isStop(target),true);
  f.machine.cancel(); f.answer(); f.doc.querySelector('.enter').classList.remove('searching'); f.check(); f.advance(801); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working','idle']); f.dom.window.close();
});
test('Zhipu guest completion uses usable feedback when both copy actions require login', () => {
  const f=setup(SITES.find(s=>s.id==='zhipu-web'));
  f.doc.querySelector('#messages').className='conversation-list';
  f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
  f.doc.querySelector('#messages').innerHTML='<div class="conversation-item"><div class="answer"><div class="interact-operate"><div class="disabled"><i class="shim copy canuse"></i></div><i class="shim thumbs-up canuse shimGrey"></i><i class="shim thumbs-down canuse"></i></div></div></div>';
  f.check(); f.finish(); f.check(); f.advance(801); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
});
test('shared final detection chooses an available control after a disabled first match', () => {
  const f=setup(SITES.find(s=>s.id==='chatgpt-web'));
  f.machine.arm(f.adapter.snapshot()); f.generating(); f.check(); f.answer();
  f.doc.querySelector('[data-testid="good-response-turn-action-button"]').disabled=true;
  f.doc.querySelector('article').insertAdjacentHTML('beforeend','<button data-testid="bad-response-turn-action-button"></button>');
  f.finish(); f.check(); f.advance(801); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working','done']); f.dom.window.close();
});
test('Qianwen content script captures Slate Enter and actual send-button clicks', async () => {
  const site=SITES.find(s=>s.id==='qianwen-web');
  for (const mode of ['enter','click','plaintext']) {
    const f=setup(site), handlers={}, outgoing=[];
    f.doc.querySelector('#busy').innerHTML='<button data-session-switch-target="send-query"><span data-icon-type="qwpcicon-sendChat"></span></button>';
    if (mode==='plaintext') f.doc.querySelector('[contenteditable]').setAttribute('contenteditable','plaintext-only');
    f.doc.addEventListener=(kind,fn)=>handlers[kind]=fn;
    let poll, inspect, state, contentNow=Date.now();
    const context={document:f.doc,location:f.dom.window.location,navigator:{onLine:true},Element:f.dom.window.Element,
      crypto:globalThis.crypto,Date:class extends Date { static now(){return contentNow;} },Promise,CustomEvent:f.dom.window.CustomEvent,FlameProviders:{...require('../aiflame/integrations/chatgpt_web/extension/providers.js'),createAdapter:()=>f.adapter},
      FlameTurn:class extends FlameTurn {constructor(...args){super(...args);state=this;}},
      window:{addEventListener(){}},MutationObserver:class{observe(){}},setTimeout(){},setInterval:fn=>poll=fn,
      chrome:{runtime:{sendMessage:async m=>{outgoing.push(m.event);return {ok:true};},onMessage:{addListener:fn=>inspect=fn}}}};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../aiflame/integrations/chatgpt_web/extension/content.js'),'utf8'),context);
    if (mode==='click') handlers.click({isTrusted:true,target:f.doc.querySelector('[data-icon-type="qwpcicon-sendChat"]')});
    else handlers.keydown({isTrusted:true,key:'Enter',target:f.doc.querySelector('[data-slate-leaf]'),isComposing:false});
    assert.ok(state.pending,'trusted send is armed');
    f.generating(); poll(); await new Promise(setImmediate);
    assert.equal(outgoing[0]?.state,'working'); assert.equal(outgoing[0]?.source_id,site.id);
    let diagnostic; inspect({kind:'inspect'},{},result=>diagnostic=result);
    assert.equal(diagnostic.active,true); assert.equal(diagnostic.busy,true);
    // Worker messages must finish even when the page's interval never fires.
    f.answer(); f.finish(); contentNow+=1000;
    inspect({kind:'tick'},{},()=>{}); contentNow+=801; inspect({kind:'tick'},{},()=>{});
    await new Promise(setImmediate);
    assert.equal(outgoing.at(-1).state,'done');
    assert.equal(state.active,null);
    f.adapter.snapshot=()=>{throw Error('diagnostic control failure');};
    f.adapter.inspectReplyLayout=()=>{throw Error('diagnostic layout failure');};
    let probe; inspect({kind:'probe'},{},result=>probe=result);
    assert.equal(probe?.source_id,site.id); assert.equal(probe?.active,false);
    f.dom.window.close();
  }
});
test('SPA loading in a live document cannot cancel the background task; actual unload still does', async () => {
  let handler, updated; const session={}, nativeEvents=[], inspections=[];
  let live=true;
  const context={URL,crypto:globalThis.crypto,Date,setTimeout:()=>1,importScripts(){},FlameProviders:require('../aiflame/integrations/chatgpt_web/extension/providers.js'),
    chrome:{runtime:{id:'extension',getURL:f=>'chrome-extension://extension/'+f,onMessage:{addListener:fn=>handler=fn},
      sendNativeMessage:(host,event,cb)=>{nativeEvents.push(event);cb({ok:true});}},
      storage:{local:{set:async()=>{}},session:{set:async value=>Object.assign(session,value),get:async key=>({[key]:session[key]}),remove:async key=>delete session[key]}},
      tabs:{onRemoved:{addListener(){}},onUpdated:{addListener:fn=>updated=fn},sendMessage:async(tab,message,options)=>{
        inspections.push(options);if(!live) throw Error('document gone');
        if(message.kind==='inspect') throw Error('diagnostic layout failure in a live document');
        assert.equal(message.kind,'probe'); return {source_id:'chatgpt-web',active:true};
      }}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../aiflame/integrations/chatgpt_web/extension/background.js'),'utf8'),context);
  const event={schema_version:'1.0',event_id:crypto.randomUUID(),source_id:'chatgpt-web',task_id:crypto.randomUUID(),seq:1,state:'working',occurred_at:new Date().toISOString(),ttl_ms:60000};
  const sender={id:'extension',frameId:0,documentId:'original-document',url:'https://chatgpt.com/',tab:{id:9}};
  await new Promise(resolve=>handler({kind:'event',event},sender,resolve));
  updated(9,{status:'loading'}); await new Promise(setImmediate);
  assert.deepEqual(nativeEvents.map(e=>e.state),['working']);
  assert.equal(inspections[0].documentId,'original-document');
  live=false; updated(9,{status:'loading'}); await new Promise(setImmediate);
  assert.deepEqual(nativeEvents.map(e=>e.state),['working','idle']);
  assert.equal(nativeEvents[1].reason,'cancelled'); assert.equal(nativeEvents[1].seq,2);
  assert.equal(nativeEvents[1].documentId,undefined);
});
test('every provider answers document probes without reading controls or running diagnostics', () => {
  for(const site of SITES) {
    const f=setup(site);let handler;
    const adapter=new Proxy({}, {get(){return ()=>{throw Error('probe must not inspect DOM');};}});
    const context={document:f.doc,location:f.dom.window.location,navigator:{onLine:true},Element:f.dom.window.Element,
      crypto:globalThis.crypto,Date,Promise,CustomEvent:f.dom.window.CustomEvent,FlameProviders:{...require('../aiflame/integrations/chatgpt_web/extension/providers.js'),createAdapter:()=>adapter},FlameTurn,
      window:{addEventListener(){}},MutationObserver:class{observe(){}},setTimeout(){},setInterval(){},
      chrome:{runtime:{onMessage:{addListener:fn=>handler=fn},sendMessage:async()=>{throw Error('probe must not emit events');}}}};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../aiflame/integrations/chatgpt_web/extension/content.js'),'utf8'),context);
    let reply;handler({kind:'probe'},{},value=>reply=value);
    assert.equal(reply.source_id,site.id);assert.equal(reply.active,false);
    assert.deepEqual(Object.keys(reply).sort(),['active','source_id']); f.dom.window.close();
  }
});
test('all providers complete via worker ticks while page polling timers remain paused',async()=>{
  for(const site of SITES){
    const f=setup(site),handlers={},events=[];let receive,now=100000;
    f.doc.addEventListener=(kind,fn)=>handlers[kind]=fn;
    const context={document:f.doc,location:f.dom.window.location,navigator:{onLine:true},Element:f.dom.window.Element,
      CustomEvent:f.dom.window.CustomEvent,crypto:globalThis.crypto,Date:class extends Date {static now(){return now;}},Promise,
      FlameProviders:{...require('../aiflame/integrations/chatgpt_web/extension/providers.js'),createAdapter:()=>f.adapter},FlameTurn,
      window:{addEventListener(){}},MutationObserver:class{observe(){}},setTimeout(){},setInterval(){},
      chrome:{runtime:{onMessage:{addListener:fn=>receive=fn},sendMessage:async m=>{events.push(m.event);return {ok:true};}}}};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../aiflame/integrations/chatgpt_web/extension/content.js'),'utf8'),context);
    handlers.keydown({isTrusted:true,key:'Enter',isComposing:false,target:f.doc.querySelector(site.composer)});
    f.generating();receive({kind:'tick'},{},()=>{});f.answer();
    for(let elapsed=0;elapsed<360000;elapsed+=5000){now+=5000;receive({kind:'tick'},{},()=>{});}
    assert.ok(events.length===0,'native forwarding runs asynchronously');
    await new Promise(setImmediate);assert.ok(events.every(e=>e.state==='working'));assert.ok(events.length>10);
    f.finish();receive({kind:'tick'},{},()=>{});now+=5000;receive({kind:'tick'},{},()=>{});await new Promise(setImmediate);
    assert.equal(events.at(-1).state,'done',site.id);assert.equal(events.filter(e=>e.state==='done').length,1);
    f.dom.window.close();
  }
});

for (const id of ['claude-web','gemini-web']) test(id + ': current localized reply actions finish only the new completed reply', () => {
  const site=SITES.find(s=>s.id===id), f=setup(site);
  const reply=id==='claude-web'
    ? '<div class="group group/message-row"><div class="contents"><div class="group relative" data-is-streaming="true"><div class="font-claude-response"></div></div></div><div role="toolbar" data-testid="message-actions"><button data-testid="action-bar-copy" class="[&:disabled:not([aria-busy])]:opacity-disabled" aria-label="复制"></button><button data-testid="action-bar-thumbs-up" class="[&:disabled:not([aria-busy])]:opacity-disabled" aria-label="回答不错"></button><button data-testid="action-bar-retry" class="[&:disabled:not([aria-busy])]:opacity-disabled" aria-label="重试"></button></div></div>'
    : '<model-response><copy-button><gem-icon-button><button aria-label="复制"></button></gem-icon-button></copy-button><thumb-up-button><gem-icon-button><button aria-label="答得好"></button></gem-icon-button></thumb-up-button><regenerate-button><gem-icon-button><button aria-label="重做"></button></gem-icon-button></regenerate-button></model-response>';
  const mount=()=>{f.doc.querySelector('#messages').insertAdjacentHTML('beforeend',reply);return f.doc.querySelector('#messages').lastElementChild;};
  const history=mount(); history.querySelector('[data-is-streaming]')?.setAttribute('data-is-streaming','false');
  f.machine.arm(f.adapter.snapshot()); f.generating(); f.check();
  f.finish(); f.check(); f.advance(1000); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working'],'history toolbar is not this reply');
  f.generating(); const current=mount(); current.setAttribute('aria-disabled','true'); f.check();
  f.finish(); f.check(); f.advance(1000); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working'],'disabled actions cannot finish');
  current.removeAttribute('aria-disabled'); current.classList.add('is-disabled');
  f.check(); f.advance(1000); f.check();
  assert.deepEqual(f.events.map(e=>e.state),['working'],'plain disabled state class still blocks completion');
  current.classList.remove('is-disabled'); f.check(); f.advance(1000); f.check();
  if(id==='claude-web') {
    assert.deepEqual(f.events.map(e=>e.state),['working'],'streaming flag blocks early toolbar');
    current.querySelector('[data-is-streaming]').setAttribute('data-is-streaming','false');
    f.check(); f.advance(801); f.check();
  }
  assert.deepEqual(f.events.map(e=>e.state),['working','done']);
  f.dom.window.close();
});
