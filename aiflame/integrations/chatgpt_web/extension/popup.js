// Follow Chrome’s UI language; task data and protocol identifiers stay literal.
const englishUI = (chrome.i18n?.getUILanguage?.() || navigator.language || '').startsWith('en');
const ENGLISH = {
  "SeeEnd 网页提醒": "SeeEnd web reminders",
  "SeeEnd · 网页提醒": "SeeEnd · Web reminders",
  "检查网页与 SeeEnd 的连接。": "Check this page’s connection to SeeEnd.",
  "检查当前页面": "Check this page",
  "详细诊断": "Diagnostics",
  "请保持 SeeEnd 运行。只检查网页控件，不读取提问和回答正文。": "Keep SeeEnd running. Only page controls are checked; prompt and answer text are not read.",
  "正在检查…": "Checking…",
  "正在检查当前网页和 SeeEnd…": "Checking this page and SeeEnd…",
  "当前页面不是支持的 AI 对话网页。": "This page is not a supported AI conversation page.",
  "请切换到 ChatGPT、Gemini、Claude、DeepSeek、千问、豆包、元宝、Kimi 或智谱清言的对话网页，再检查。": "Open a conversation in ChatGPT, Gemini, Claude, DeepSeek, Qwen, Doubao, Yuanbao, Kimi, or Zhipu, then check again.",
  "网页还没有准备好。": "The page is not ready.",
  "请打开支持的 AI 对话网页；如果已经打开，请刷新这个网页，再点击「检查当前页面」。刚安装或更新扩展后，已打开的网页需要刷新。": "Open a supported AI conversation page. If it is already open, refresh it and click “Check this page”. Existing tabs need a refresh after installing or updating the extension.",
  "页面检查错误": "Page check error",
  "无法识别页面来源": "Could not identify the website",
  "暂时无法连接 SeeEnd。": "Cannot connect to SeeEnd yet.",
  "打开 SeeEnd 桌面 App，在「应用接入 → 安装浏览器扩展」的第 4 步重新允许连接，然后刷新这个网页。": "Open the SeeEnd desktop App. In “App integrations → Install browser extension”, allow the connection again in step 4, then refresh this page.",
  "连接错误": "Connection error",
  "未收到响应": "No response",
  "已连接 SeeEnd，但该平台的提醒已关闭。": "Connected to SeeEnd, but reminders for this website are off.",
  "在 SeeEnd「应用接入」打开 ": "In SeeEnd’s “App integrations”, turn on the switch for ",
  " 的开关，再发送一条新提问。": ", then send a new prompt.",
  "已连接 SeeEnd，但提醒当前已暂停或静默。": "Connected to SeeEnd, but reminders are paused or muted.",
  "在 SeeEnd 恢复提醒或调整静默时段，再发送一条新提问。": "Restore reminders or adjust quiet hours in SeeEnd, then send a new prompt.",
  "已连接 SeeEnd，当前页面还不能确认接入。": "Connected to SeeEnd, but this page cannot confirm the integration yet.",
  "请进入普通文字对话并刷新网页后重试；当前模式可能尚不支持。": "Open an ordinary text conversation, refresh the page, then try again. This mode may not be supported.",
  "已连接 SeeEnd，页面检查通过。": "Connected to SeeEnd. Page check passed.",
  "正在跟踪这条任务，等待回答完成。": "Tracking this task and waiting for completion.",
  "可以开始一条新任务。": "You can start a new task.",
  "请发送一条新提问，等回答完成后到 SeeEnd 查看开始与完成记录。页面检查通过还不代表真实任务已验证。": "Send a new prompt. When the answer finishes, check its start and completion in SeeEnd. A passing page check does not yet verify a real task.",
  "扩展版本": "Extension version",
  "最近事件": "Latest event",
  "尚无事件": "No events yet",
  "这次检查未完成。": "This check did not finish.",
  "请关闭这个弹窗后重试；若仍失败，刷新 AI 网页并确认 SeeEnd 正在运行。": "Close this popup and try again. If it still fails, refresh the AI page and keep SeeEnd running.",
  "检查错误": "Check error",
  "千问": "Qwen",
  "豆包": "Doubao",
  "元宝": "Yuanbao",
  "智谱清言": "Zhipu",
  "：": ": "
};
const phrases = Object.keys(ENGLISH).sort((a,b) => b.length - a.length);
const pattern = new RegExp(phrases.map(key => key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'g');
function tr(text) { return englishUI ? text.replace(pattern, match => ENGLISH[match]) : text; }
if (englishUI) {
  document.documentElement.lang = 'en';
  document.title = tr(document.title);
  for (const id of ['heading','intro','check','status','diagnostics-label','privacy']) {
    const element = document.getElementById(id);
    element.textContent = tr(element.textContent);
  }
}
async function check() {
  const output = document.getElementById('status');
  const next = document.getElementById('next');
  const details = document.getElementById('details');
  const button = document.getElementById('check');
  button.disabled = true;
  output.textContent = tr('正在检查当前网页和 SeeEnd…'); next.textContent = ''; details.replaceChildren();
  function show(status, action) { output.textContent = tr(status); next.textContent = tr(action); }
  function detail(name, value) {
    const line = document.createElement('div'); line.textContent = tr(name + '：') + (typeof value === 'string' ? tr(value) : JSON.stringify(value)); details.appendChild(line);
  }
  try {
    const tabs = await chrome.tabs.query({active:true, currentWindow:true});
    const tab = tabs[0];
    let site = FlameProviders.siteFor(tab?.url || '');
    if (tab?.url && !site) {
      show('当前页面不是支持的 AI 对话网页。', '请切换到 ChatGPT、Gemini、Claude、DeepSeek、千问、豆包、元宝、Kimi 或智谱清言的对话网页，再检查。');
      return;
    }
    let page;
    try { page = await chrome.tabs.sendMessage(tab.id, {kind:'inspect'}); }
    catch (error) {
      show((site ? site.name + '：' : '') + '网页还没有准备好。', '请打开支持的 AI 对话网页；如果已经打开，请刷新这个网页，再点击「检查当前页面」。刚安装或更新扩展后，已打开的网页需要刷新。');
      detail('页面检查错误', String(error)); return;
    }
    site = site || FlameProviders.SITES.find(item => item.id === page.source_id);
    if (!site) throw new Error('无法识别页面来源');
    let bridge;
    try { bridge = await chrome.runtime.sendMessage({kind:'check', source_id:page.source_id}); }
    catch (error) { bridge = {ok:false, detail:String(error)}; }
    if (!bridge?.ok) {
      show(site.name + '：暂时无法连接 SeeEnd。', '打开 SeeEnd 桌面 App，在「应用接入 → 安装浏览器扩展」的第 4 步重新允许连接，然后刷新这个网页。');
      detail('连接错误', bridge?.detail || bridge?.error || '未收到响应');
    } else if (!bridge.source_enabled) {
      show(site.name + '：已连接 SeeEnd，但该平台的提醒已关闭。', '在 SeeEnd「应用接入」打开 ' + site.name + ' 的开关，再发送一条新提问。');
    } else if (bridge.muted) {
      show(site.name + '：已连接 SeeEnd，但提醒当前已暂停或静默。', '在 SeeEnd 恢复提醒或调整静默时段，再发送一条新提问。');
    } else if (page.disabled || !page.recognized) {
      show(site.name + '：已连接 SeeEnd，当前页面还不能确认接入。', '请进入普通文字对话并刷新网页后重试；当前模式可能尚不支持。');
    } else {
      show(site.name + '：已连接 SeeEnd，页面检查通过。\n' + (page.active ? '正在跟踪这条任务，等待回答完成。' : '可以开始一条新任务。'),
        '请发送一条新提问，等回答完成后到 SeeEnd 查看开始与完成记录。页面检查通过还不代表真实任务已验证。');
    }
    const saved = await chrome.storage.local.get('result_' + page.source_id);
    detail('扩展版本', page.version || FlameProviders.VERSION);
    detail('最近事件', saved['result_' + page.source_id] || '尚无事件');
    for (const key of ['recognized','busy','active','pending','final_controls','composer_ready','reply_matches_active','new_final_controls','disabled','last_transition','reply_samples','busy_controls','reply_structure','reply_layout','reply_ancestors','render_state','composer_controls','page_controls']) {
      if (page[key] !== undefined) detail(key, page[key]);
    }
  } catch (error) {
    // Preserve a useful partial check if optional diagnostics fail.
    if (output.textContent === tr('正在检查当前网页和 SeeEnd…')) show('这次检查未完成。', '请关闭这个弹窗后重试；若仍失败，刷新 AI 网页并确认 SeeEnd 正在运行。');
    detail('检查错误', String(error));
  } finally { button.disabled = false; }
}
document.getElementById('check').onclick = check;
check();
