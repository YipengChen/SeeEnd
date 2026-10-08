async function check() {
  const output = document.getElementById('status');
  try {
    const tabs = await chrome.tabs.query({active:true, currentWindow:true});
    const page = await chrome.tabs.sendMessage(tabs[0].id, {kind:'inspect'});
    const bridge = await chrome.runtime.sendMessage({kind:'check', source_id:page.source_id});
    const connection = '当前平台：' + page.site + '\n本机桥接：' + (bridge?.ok ? '已连接' : '不可达，请安装 / 修复桥接并启动 Hub')
      + (!bridge?.ok ? '\n原因：' + (bridge?.detail || bridge?.error || '未收到桥接响应') : '')
      + (bridge?.ok ? '\n开发板：' + (bridge.device_online ? '在线' : '离线')
        + '\n触发：' + (!bridge.source_enabled ? '该平台来源已停用' : bridge.muted ? '已暂停或静默' : '已启用') : '');
    const resultKey = 'result_' + page.source_id;
    const saved = await chrome.storage.local.get(resultKey);
    const last = saved[resultKey];
    output.textContent = connection + '\n页面控件：' + (page.recognized ? '已识别' : '未识别，请勿视为支持')
      + '\n生成状态：' + (page.busy ? '正在生成' : '未生成')
      + '\n任务跟踪：' + (page.active ? '进行中' : page.pending ? '已捕获发送，等待生成控件' : '未跟踪')
      + '\n回复结束控件：' + (page.final_controls ? '已识别' : '尚未出现或未识别')
      + '\n输入框状态：' + (page.composer_ready ? '可用' : '不可用或未识别')
      + (page.active ? '\n当前回复跟踪：' + (page.reply_matches_active === null ? '等待回复标识' : page.reply_matches_active ? '匹配' : '不匹配')
        + '\n本次结束控件：' + (page.new_final_controls ? '已识别' : '尚未出现或仍为历史控件') : '')
      + '\n适配器：' + (page.disabled ? '已暂停，请刷新页面' : '运行中')
      + '\n版本：' + page.version
      + (page.last_transition ? '\n最近结束原因：' + page.last_transition.cause : '')
      + (last?.cancel_origin ? '\n后台取消原因：' + last.cancel_origin : '')
      + '\n最近送达：' + (last ? (last.ok ? last.result : last.error) : '该平台尚无事件')
      + '\n接收成功不代表设备已发光；设备回执请查看本地控制台。';
    const details = document.getElementById('details'); details.replaceChildren();
    for (const [name, values] of Object.entries({reply_samples:page.reply_samples,
      busy_controls:page.busy_controls, reply_structure:page.reply_structure, reply_layout:page.reply_layout,
      reply_ancestors:page.reply_ancestors, render_state:page.render_state,
      composer_controls:page.composer_controls,
      page_controls:page.page_controls})) {
      for (const value of Array.isArray(values) ? values : [values]) {
        const line = document.createElement('div'); line.textContent = name + ': ' + JSON.stringify(value);
        details.appendChild(line);
      }
    }
  } catch {
    output.textContent = '请打开或刷新支持的 AI 对话网页，再检查当前页面。\n支持 ChatGPT、Claude、Gemini、DeepSeek、千问、豆包、元宝、Kimi、智谱清言。';
  }
}
document.getElementById('check').onclick = check;
check();
