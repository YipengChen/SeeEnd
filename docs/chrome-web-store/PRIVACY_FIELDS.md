# SeeEnd 隐私和权限填写说明

> 0.2.13 本地权限说明草稿，与当前源码对齐。history/ 和 prepared/0.2.10/ 保留当时提交资料；本文件更新不表示后台字段已保存或政策已公开部署。

## 单一用途

识别用户在支持的 AI 文字对话网页中主动发起任务的生成与结束控件，将状态事件通过 Native Messaging 传给本机 SeeEnd 桌面应用，以执行用户配置的任务提醒和用户主动返回任务。

## 权限理由

nativeMessaging：仅与本机 com.aiflame.chatgpt 原生消息宿主通信，传输状态事件并查询 SeeEnd 连接、来源开关和设备状态。桌面应用负责安装桥接。扩展不直接访问设备，不访问其他原生应用。

storage：chrome.storage.session 保存浏览器会话内的活跃任务标识与状态，用于后台检查及页面关闭取消；chrome.storage.local 保存每个平台最近一次送达结果和最近一次总结果，供弹窗显示。另保存用户主动返回任务所需的会话地址和原标签页位置，最多七天／1,000 项。没有对话正文存储。

支持网站访问范围：content_scripts.matches 限定在九个平台的 HTTPS 网页。需要检查输入区是否可用、发送／停止／结束控件是否存在及启用，以及会话路由变化。域名清单以 extension-upload.zip 中 manifest.json 为准，没有所有网站通配权限。

网页执行环境：千问和智谱清言还使用 MAIN world 脚本辅助已登记后台任务的渲染调度，以便结束控件能出现；不读取网络响应、提示词或回答正文。这项行为是扩展功能的一部分，应向审核员解释。

## 远程代码

选择“不使用远程代码”。扩展脚本均随 ZIP 分发；Native Messaging 传输结构化数据，不下载或执行开发者托管的脚本。今后如改变实现，需要重新评估。

## 数据类别

按当前代码采取充分披露的填写方式，建议勾选以下类别，并在说明中限定其范围；提交前核对后台最新字段定义。

- 网站内容：仅处理 DOM 控件的结构、属性、存在性和可用状态；不读取提问、回答正文或编辑器内容。
- 用户活动：识别发送、Enter 提交、停止及页面离开等交互；不记录用户输入字符，不采集通用键盘日志。
- 网页浏览记录：仅在支持网站当前页面内处理地址及会话路由，以区分平台和导航；不读取浏览器历史数据库；0.2.11 本地草稿将发生过任务的会话地址和原标签页位置保存到扩展本地，最多七天／1,000 项，任务更新或返回时清理。地址不发送给桌面应用或开发者服务器。

不勾选与实现无关的身份信息、健康、财务支付、认证信息、位置或个人通信正文类别。这里披露的是本地功能处理范围，不能据此写成“读取完整聊天内容”。

## 数据用途承诺

这些状态和交互数据仅用于扩展所声明的任务提醒功能，不出售、不用于广告、不用于信用评估或借贷。依据实际用途确认后台 Limited Use 承诺，保持后台、隐私政策、商店文案与实现一致。

隐私政策链接：https://yipengchen.github.io/SeeEnd-downloads/。不要把本地文件路径填进后台；需先公开托管 privacy/index.html。

## 英文权限理由

Single purpose: Observe user-initiated text-chat generation and explicit completion controls on supported AI websites, and send status events to the local SeeEnd desktop app for configured task reminders and user-initiated returns to the original conversation.

nativeMessaging: Communicate only with the local com.aiflame.chatgpt native messaging host to send status events, check connection, source and device status, and receive user-initiated return requests for the original browser profile. The desktop app installs the bridge; the extension does not control hardware directly.

storage: Keep active task state in session storage for background checks and cancellation, and the latest delivery result per source in local storage for the popup. Also retain up to 1,000 task return destinations (conversation URL, tab identity and timestamp) for seven days, cleaned on task updates and returns. No prompt or response text is stored.

Site access: Access only the listed HTTPS AI sites to inspect UI controls and local route changes. No browser-history database access, credential access or conversation-text access. Qianwen and Zhipu additionally use a bundled MAIN-world script to help pending background rendering complete during a tracked task.

Data categories: UI structure and control attributes; submit/stop/navigation actions; current supported-page URLs processed locally and tracked-task conversation URLs retained in extension-local storage for user-initiated returns. Only opaque identifiers and status events are sent to the local app. Actual URLs and site reply identifiers are excluded from events.

官方说明：[隐私字段](https://developer.chrome.com/docs/webstore/cws-dashboard-privacy)、[用户数据 FAQ](https://developer.chrome.com/docs/webstore/program-policies/user-data-faq/)。
