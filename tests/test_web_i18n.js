const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { JSDOM } = require("jsdom");
const settle = () => new Promise(setImmediate);

async function fixture(language = "en", fail = false) {
  const dom = new JSDOM(`<html lang="zh-CN"><head><title>SeeEnd</title></head><body>
    <select id="languageChoice"><option value="zh-CN">中文</option><option value="en">English</option></select>
    <p id="status">工作中</p><p id="message"></p><input id="host" value="我的设备" placeholder="灯具 IP，例如 192.168.1.100">
    <pre>连接设备</pre><span data-user-content>我的设备</span></body></html>`, {runScripts: "outside-only"});
  const context = dom.getInternalVMContext();
  const calls = [];
  context.fetch = async (path, options) => {
    calls.push({path, options});
    return {ok: !(fail && options), json: async () => path === "/locales/en.json"
      ? JSON.parse(fs.readFileSync("aiflame/assets/web/locales/en.json", "utf8"))
      : options ? {error: "请求失败"} : {settings: {language}}};
  };
  vm.runInContext(fs.readFileSync("aiflame/assets/web/i18n.js", "utf8").replaceAll("export ", ""), context);
  await settle();
  return {dom, context, calls, get: (id) => dom.window.document.getElementById(id)};
}

test("web language changes static and live text reversibly while preserving user values and code", async () => {
  const f = await fixture();
  assert.equal(f.dom.window.document.documentElement.lang, "en");
  assert.equal(f.get("status").textContent, "Working");
  assert.equal(f.get("host").value, "我的设备");
  assert.equal(f.get("host").placeholder, "Lamp IP, e.g. 192.168.1.100");
  assert.equal(f.dom.window.document.querySelector("pre").textContent, "连接设备");
  assert.equal(f.dom.window.document.querySelector("[data-user-content]").textContent, "我的设备");
  f.get("status").textContent = "演示剩余 7 秒";
  await settle();
  assert.equal(f.get("status").textContent, "Demo: 7 seconds remaining");
  vm.runInContext("setLanguage('zh-CN')", f.context);
  assert.equal(f.get("status").textContent, "演示剩余 7 秒");
  assert.equal(f.get("host").placeholder, "灯具 IP，例如 192.168.1.100");
  vm.runInContext("observer.disconnect()", f.context);
  f.dom.window.close();
});

test("web selector saves preference and rolls back after rejected save", async () => {
  for (const fail of [false, true]) {
    const f = await fixture("zh-CN", fail);
    f.get("languageChoice").value = "en";
    f.get("languageChoice").dispatchEvent(new f.dom.window.Event("change"));
    await settle();
    const saved = f.calls.find((call) => call.options);
    assert.deepEqual(JSON.parse(saved.options.body), {language: "en"});
    assert.equal(f.get("languageChoice").disabled, false);
    assert.equal(f.get("languageChoice").value, fail ? "zh-CN" : "en");
    assert.equal(f.get("status").textContent, fail ? "工作中" : "Working");
    vm.runInContext("observer.disconnect()", f.context);
  f.dom.window.close();
  }
});
