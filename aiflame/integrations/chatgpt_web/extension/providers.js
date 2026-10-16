/* Site-specific control adapters. Never inspect message text, editor values or account data. */
(function (root) {
  'use strict';
  const VERSION = '0.2.13';
  function labels(values) {
    return values.flatMap(value => ['aria-label', 'title', 'data-tooltip'].map(
      attr => `button[${attr}="${value}"],[role="button"][${attr}="${value}"],a[${attr}="${value}"],div[${attr}="${value}"]`
    )).join(',');
  }
  const stop = labels(['Stop','Stop generating','Stop streaming','停止','停止生成','停止回答','停止回复','停止对话','停止流式传输']);
  const send = labels(['Send','Send prompt','发送','发送消息','发送提示']);
  const retry = labels(['Regenerate','Regenerate response','重新生成','重新生成回复','重新回答','重试']);
  const copy = labels(['Copy','Copy response','复制','复制回复']);
  const feedback = labels(['Good response','Bad response','回复不错','回复不好','点赞','点踩','喜欢','不喜欢']);
  const join = (...values) => values.filter(Boolean).join(',');
  const SITES = [
    {id:'chatgpt-web', name:'ChatGPT 网页版', origins:['https://chatgpt.com'],
      composer:'#prompt-textarea,textarea[name="prompt-textarea"],main [contenteditable="true"][role="textbox"],main [contenteditable="true"]',
      stop:join('button[data-testid="stop-button"]',stop), send:join('button[data-testid="send-button"]',send),
      retry:join('button[data-testid="regenerate-response-button"]',retry),
      turn:'[data-message-author-role="assistant"]', turnContainer:'article,[data-testid^="conversation-turn-"]',
      // The redesigned ChatGPT UI also uses this class for user-message actions.
      // Only assistant copy controls identify a reply; user copy/edit rows cannot.
      fallbackTurn:'main .turn-action-controls:has(' + join('button[data-testid="copy-turn-action-button"]',copy) + ')',
      copy:join('button[data-testid="copy-turn-action-button"]',copy),
      final:join('button[data-testid="good-response-turn-action-button"],button[data-testid="bad-response-turn-action-button"]',feedback,retry),
      error:'[data-testid="conversation-turn-error"]',
      newRoute:/^\/$|^\/g\/[^/]+(?:\/project)?\/?$/, conversationRoute:/^\/(?:g\/[^/]+\/)?c\//,
      temporaryRoute:/^\/(?:g\/[^/]+\/)?c\/local-chatgpt(?::|%3a)/i},
    {id:'deepseek-web', name:'DeepSeek 网页版', origins:['https://chat.deepseek.com'],
      composer:'#chat-input,textarea[placeholder*="DeepSeek"]',
      // DeepSeek's icon-only button exposes no label until hovered. Match its published stop SVG.
      stop:join('._52c986b:has(path[d^="M2 4.88C2 3.68009"])',stop),
      send:join('._52c986b:not(:has(path[d^="M2 4.88C2 3.68009"]))',send), retry,
      turn:'.ds-message:has(.ds-assistant-message-main-content)',
      // The finished toolbar is a sibling of .ds-message, inside this response wrapper.
      turnContainer:'._4f9bf79',
      actionRow:'._965abe9', actionControls:'.ds-button', minimumControls:3,
      error:'[data-status="error"],[data-status="failed"],.ds-message-error',
      newRoute:/^\/$/, conversationRoute:/^\/(?:a\/chat\/s\/|chat\/|c\/)/},
    {id:'qianwen-web', name:'千问 网页版', backgroundFrames:true, origins:['https://www.qianwen.com','https://qianwen.com','https://tongyi.aliyun.com'],
      composer:'.text-area-slot-container textarea,.text-area-slot-container [contenteditable="true"],.text-area-slot-container [contenteditable="plaintext-only"],[data-chat-input-shell="true"] [data-slate-editor="true"],textarea[placeholder*="千问"],textarea[placeholder*="通义"]',
      stop, send:join('[data-session-switch-target="send-query"]',send), retry:join('[data-answer-feedback-toolbar] div:has(> svg path[d^="M14.949 9.059"])','button:has([data-icon-type="qwpcicon-regenerate"]),[role="button"]:has([data-icon-type="qwpcicon-regenerate"])',retry),
      turn:'.chat-answers-card-wrap,[class*="message-select-wrapper-answer-"],.answerItem',
      copy:join('[data-answer-feedback-toolbar] svg:has(path[d^="M832 64a96"])','[data-icon-type="qwpcicon-copy"],[data-icon-type="tongyi-copy-line"]',copy),
      final:join('[data-answer-feedback-toolbar] svg:has(path[d^="M2.5 14.049"]),[data-answer-feedback-toolbar] svg:has(path[d^="M2.5 2h8.77"]),[data-answer-feedback-toolbar] svg:has(path[d^="M14.949 9.059"])','[data-icon-type^="qwpcicon-thumbs"],[data-icon-type^="tongyi-thumbs"],[data-icon-type="qwpcicon-regenerate"]',feedback,retry),
      error:'[data-status="error"],[data-status="failed"],[data-session-switch-error]',
      newRoute:/^\/(?:qianwen\/?)?$/, conversationRoute:/^\/(?:chat\/|qianwen)/},
    {id:'doubao-web', name:'豆包 网页版', origins:['https://www.doubao.com'],
      composer:'[data-testid="chat_input_input"],[data-testid="chat_input"] textarea,[data-testid="chat_input"] [contenteditable="true"]',
      stop:join('[data-testid="chat_input_local_break_button"]',stop),
      send:join('[data-testid="chat_input_send_button"]',send), retry:join('[data-testid="message_action_regenerate"]',retry),
      turn:'[data-testid="receive_message"],[data-testid="message_content"]:has([data-testid="message_action_copy"]),[data-testid="message-block-container"]:has([data-testid="message_action_copy"])',
      // The inner receive_message node is replaced during new-chat creation.
      // Its outer target item keeps this reply's identity and scopes its controls.
      fallbackTurn:'[data-testid="message_action_copy"]', turnContainer:'[data-target-id]', identityAttrs:['data-target-id'],
      fallbackScopeControls:true,
      copy:join('[data-testid="message_action_copy"]',copy),
      final:join('[data-testid="message_action_regenerate"],[data-testid="message_action_like"],[data-testid="message_action_dislike"]',feedback,retry),
      // Guest replies omit copy/feedback actions and show follow-up controls instead.
      completedControls:'[data-testid="suggest_message_list"]:has([data-testid="suggest_message_item"])',
      error:'[data-testid="message_error"],[data-status="failed"]',
      newRoute:/^\/chat\/?$/, conversationRoute:/^\/chat\/[^/]+/,
      temporaryRoute:/^\/chat\/local_[0-9]+\/?$/},
    {id:'yuanbao-web', name:'腾讯元宝 网页版', origins:['https://yuanbao.tencent.com'],
      composer:'.chat-input-editor [contenteditable="true"],.chat-input-editor[contenteditable="true"],.yb-input-box-textarea [contenteditable="true"],.hyc-input-box [contenteditable="true"],#searchbar-editor [contenteditable="true"],#searchbar-editor[contenteditable="true"]',
      stop:join('#yuanbao-send-btn[aria-label="停止回答"],#yuanbao-send-btn:has(img[src*="stop-chat-icon"]),.style__send-btn___RwTm5:has(.icon-stop),.style__send-btn___RwTm5:has(.icon-yb-ic_stop_fill_16),.style__send-btn___RwTm5:has([class*="stopIcon"])',stop),
      send:join('#yuanbao-send-btn:not([aria-label="停止回答"]):not(:has(img[src*="stop-chat-icon"]))',send), retry,
      turn:'.agent-chat__list__item--ai,.agent-chat__bubble--ai',
      copy:join('.agent-chat__toolbar .icon-copy,.agent-chat__toolbar .icon-copy2,[class*="toolbar"] [class*="copyButton"]',copy),
      final:join('.agent-chat__toolbar .icon-like,.agent-chat__toolbar .icon-dislike,[class*="toolbar"] [class*="likeButton"],[class*="toolbar"] [class*="unlikeButton"]',feedback,retry),
      error:'[data-status="error"],[data-status="failed"],.agent-chat__error',
      newRoute:/^\/(?:chat(?:\/[^/]+)?\/?)?$/, conversationRoute:/^\/(?:chat\/[^/]+\/[^/]+|conversation\/)/},
    {id:'kimi-web', name:'Kimi 网页版', origins:['https://www.kimi.com','https://kimi.com','https://kimi.moonshot.cn'],
      composer:'.chat-input-editor [contenteditable="true"],.chat-input-editor[contenteditable="true"],.chat-editor [contenteditable="true"]',
      stop:join('.send-button-container.stop',stop), busy:'.send-button-container.loading,.segment-assistant .segment-pending-loading',
      send:join('.send-button-container:not(.stop):not(.loading)',send),
      retry:join('.segment-assistant-actions .icon-button:has(path[d^="M303.104 315.392c114.688"])',retry),
      turn:'.segment-assistant', actionRow:'.segment-assistant-actions-content', actionControls:'.icon-button', minimumControls:3,
      error:'.segment-error',
      newRoute:/^\/(?:chat\/new)?\/?$/, conversationRoute:/^\/chat\/[^/]+/,
      temporaryRoute:/^\/chat\/p[0-9a-f]{7}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/},
    {id:'zhipu-web', name:'智谱清言 网页版', backgroundFrames:true, origins:['https://chatglm.cn'],
      composer:'.search textarea,.prosemirror-editor [contenteditable="true"],.prosemirror-editor[contenteditable="true"]',
      stop:join('button[ga-data="stop"],.enter.is-main-chat.searching',stop),
      send:join('.search-operation .enter_icon,.enter.is-main-chat:not(.searching)',send),
      retry:join('button[ga-data="retry"]',retry), turn:'.glm-3-conversation .answer,.conversation-list .answer',
      turnContainer:'.conversation-item',
      turnBusy:'.answer-loading',
      // Feedback controls exist during streaming but become usable only after it ends.
      copy:'.interact-operate .copy.canuse,.interact-operate .copy-icon,.interact-operate .copy-btn,.interact-operate .copyText',
      final:'.interact-operate .thumbs-up.canuse,.interact-operate .thumbs-down.canuse',
      // Guest copy actions can be disabled while feedback carries the explicit
      // !isSearching state. A usable current-reply feedback control is terminal.
      completedControls:'.interact-operate .thumbs-up.canuse,.interact-operate .thumbs-down.canuse',
      error:'.answer-error-icon,[data-status="error"]',
      newRoute:/^\/(?:main\/(?:alltoolsdetail|detail|gdetail\/[^/?]+))?$/, conversationRoute:/^\/main\/(?:detail|gdetail\/|alltoolsdetail\?cid=)/},
    {id:'claude-web', name:'Claude 网页版', origins:['https://claude.ai'],
      composer:'.ProseMirror[contenteditable="true"],[contenteditable="true"][role="textbox"]',
      stop:join(labels(['Stop response','停止响应']),stop), send:join(labels(['Send message']),send), retry,
      // Current Claude renders actions beside the streaming node in the message row.
      // The streaming node itself also has .group, so closest('.group') misses them.
      turn:'[class~="group/message-row"]:has([data-is-streaming],.font-claude-response),[data-is-streaming],.font-claude-response', turnContainer:'[data-testid="assistant-message"],.group',
      turnBusy:'[data-is-streaming="true"]',
      copy:join('button[data-testid="action-bar-copy"]',labels(['Copy','Copy response']),copy),
      final:join('button[data-testid="action-bar-thumbs-up"],button[data-testid="action-bar-thumbs-down"],button[data-testid="action-bar-retry"]',labels(['Retry','Good response','Bad response','回答不错','回复不佳']),feedback,retry),
      error:'[data-testid="message-error"],[data-status="error"]',
      newRoute:/^\/(?:new|project\/[^/]+)?\/?$/, conversationRoute:/^\/chat\/[^/]+/},
    {id:'gemini-web', name:'Gemini 网页版', origins:['https://gemini.google.com'],
      composer:'.ql-editor[contenteditable="true"],rich-textarea [contenteditable="true"]',
      stop:join('button.stop-button',labels(['Stop response','停止回答']),stop), send:join('button.send-button',send), retry,
      turn:'model-response', copy:join('copy-button button,button[data-test-id="copy-button"]',copy),
      final:join('thumb-up-button button,thumb-down-button button,regenerate-button button,button[data-test-id="thumb-up-button"],button[data-test-id="thumb-down-button"]',labels(['答得好','答得不好','重做']),feedback,retry),
      // Gemini also sets complete after an interrupted reply. This marker ends
      // generation, but without reply actions it must never mean success.
      settled:'.response-footer.complete',
      error:'[data-test-id="response-error"],[data-status="error"]',
      newRoute:/^\/app\/?$/, conversationRoute:/^\/app\/[^/]+/},
  ];
  function siteFor(url) {
    try { const origin = new URL(url).origin; return SITES.find(site => site.origins.includes(origin)) || null; }
    catch { return null; }
  }
  function routeFor(location) {
    const url = new URL(location.href);
    const params = new URLSearchParams();
    for (const key of ['cid','chatId','conversationId','sessionId','id']) {
      if (url.searchParams.has(key)) params.set(key, url.searchParams.get(key));
    }
    return url.pathname + (params.size ? '?' + params : '') + (url.hash.startsWith('#/') ? url.hash : '');
  }
  function routePolicy(site) {
    return {
      sourceId:site.id,
      isNewRoute:route => site.newRoute.test(route),
      canTransition:(from,to) => (site.newRoute.test(from) || site.temporaryRoute?.test(from)) && site.conversationRoute.test(to),
      isTemporaryRoute:route => !!site.temporaryRoute?.test(route),
    };
  }
  function createAdapter(site, doc, location, online, isVisible) {
    const detector = root.FlameDetector || require('./detector.js');
    return detector.createAdapter(site, doc, location, online, isVisible, routeFor);
  }
  const api = {SITES, VERSION, siteFor, routeFor, routePolicy, createAdapter};
  root.FlameProviders = api;
  if (typeof module !== 'undefined') module.exports = api;
})(globalThis);
