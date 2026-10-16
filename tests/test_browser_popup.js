const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');
const providers = require('../aiflame/integrations/chatgpt_web/extension/providers.js');
async function popup(options={}) {
  const dom = new JSDOM(fs.readFileSync('aiflame/integrations/chatgpt_web/extension/popup.html','utf8'), {runScripts:'outside-only'});
  const context = dom.getInternalVMContext();
  context.FlameProviders = providers;
  context.chrome = {
    i18n:{getUILanguage:()=>options.language || "zh-CN"},
    tabs:{query:async()=>[{id:1,url:options.hideUrl ? undefined : (options.url || 'https://chatgpt.com/')}],sendMessage:async()=>{if(options.pageError)throw Error('not injected'); return {source_id:'chatgpt-web',site:'ChatGPT',recognized:true,version:providers.VERSION,...options.page};}},
    runtime:{sendMessage:async()=>options.bridge || {ok:true,source_enabled:true,device_online:false,muted:false}},
    storage:{local:{get:async()=>({})}}
  };
  vm.runInContext(fs.readFileSync('aiflame/integrations/chatgpt_web/extension/popup.js','utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  return dom;
}
test('freshly installed page gets a refresh instruction instead of a misleading unsupported-site error',async()=>{
  const d=await popup({pageError:true}); assert.match(d.window.document.getElementById('next').textContent,/刷新/); assert.match(d.window.document.getElementById('status').textContent,/还没有准备好/); assert.equal(d.window.document.getElementById('check').disabled,false); d.window.close();
});
test('Chrome extensions page gets a clear switch-to-AI-page instruction',async()=>{
  const d=await popup({url:'chrome://extensions/'}); assert.match(d.window.document.getElementById('status').textContent,/不是支持/); assert.match(d.window.document.getElementById('next').textContent,/对话网页/); d.window.close();
});
test('a connected page invites a new task without verification labels or a lamp requirement',async()=>{
  const d=await popup(); const text=d.window.document.getElementById('status').textContent; assert.match(text,/页面检查通过/); assert.doesNotMatch(text,/开发板|灯具|离线/); const next=d.window.document.getElementById('next').textContent; assert.match(next,/自动提醒/); assert.doesNotMatch(text+next,/验证/); d.window.close();
});
test('unreachable desktop App points to the exact setup step and keeps technical errors in details',async()=>{
  const d=await popup({bridge:{ok:false,error:'bridge_update_required'}}); assert.match(d.window.document.getElementById('next').textContent,/第 2 步/); assert.doesNotMatch(d.window.document.getElementById('status').textContent,/Hub|桥接|bridge/); assert.match(d.window.document.getElementById('details').textContent,/bridge_update_required/); d.window.close();
});
test('source-disabled pages explain how to enable reminders',async()=>{
  const d=await popup({bridge:{ok:true,source_enabled:false}}); assert.match(d.window.document.getElementById('next').textContent,/开关/); d.window.close();
});
test('lamp suppression does not block browser integration guidance for independent notification channels',async()=>{
  const d=await popup({bridge:{ok:true,source_enabled:true,muted:true}}); assert.match(d.window.document.getElementById('status').textContent,/页面检查通过/); assert.doesNotMatch(d.window.document.getElementById('next').textContent,/暂停|静默|恢复提醒/); d.window.close();
});

test('a hidden tab URL does not reject a supported page or require broader browser permissions',async()=>{
  const d=await popup({hideUrl:true}); assert.match(d.window.document.getElementById('status').textContent,/页面检查通过/); d.window.close();
});

test('English Chrome uses readable English controls, outcomes and recovery text', async()=>{
  for (const options of [{},{pageError:true},{bridge:{ok:false,error:'bridge_update_required'}},{bridge:{ok:true,source_enabled:false}},{bridge:{ok:true,source_enabled:true,muted:true}}]) {
    const d=await popup({...options,language:'en'}); const document=d.window.document;
    assert.equal(document.documentElement.lang,'en');
    assert.equal(document.getElementById('check').textContent,'Check this page');
    for(const id of ['heading','intro','status','next','diagnostics-label','privacy']) assert.doesNotMatch(document.getElementById(id).textContent,/[\u4e00-\u9fff]/);
    d.window.close();
  }
});
