# Claw Light 与 Future Device 软件方案研究

> 历史资料：保留原版本验收结果或设计调研，不代表当前支持范围。AI Flame 0.1 / 0.2 已于 2026-10-09 移除，旧实现见 [归档分支](https://github.com/YipengChen/SeeEnd/tree/archive/ai-flame-2026-10-09)。当前操作与支持设备以仓库 README 和使用指南为准。

研究日期：2026-09-28。用途：为 SeeEnd 软件需求文档提供实现参考与变更建议。

本研究核对官方产品资料、文档及可访问的 ClawLight CLI 源码。未购买或连接两款设备，未运行其程序。源码链接指向 main，后续可能变化；官网、文档与代码存在部分表述差异，以下按证据层级区分。现有 PRD 未直接改写。

## 结论

Claw Light 值得重点参考软件架构和逐工具适配。Future Device Status Light 提供的是硬件和厂商工具，公开资料尚不足以复现其完整控制链路。两者均不能证明“监听系统通知即可兼容所有 AI App”。

SeeEnd 应继续采用 Trigger Hub，但把原生 Hook／插件作为独立的一等触发源。通知监听负责补充覆盖，浏览器扩展负责网页用户，Webhook／API 负责主动集成。产品卖点可以是面向普通用户的易安装、可配置和可解释的触发体验，而不只是能被脚本控制的灯。

## 两款产品对比

| 维度 | Claw Light | Future Device Status Light |
| --- | --- | --- |
| 已公开软件 | Rust CLI、终端仪表盘、托盘或菜单栏、状态管理和串口驱动 | 厂商 utility 的产品描述；本次未找到可复现的完整接口资料 |
| 事件来源 | Claude Code 等工具的 Hook，opencode 插件，统一事件入口 | 用户自己连接任务 Hook 或脚本 |
| 状态处理 | 按会话保存状态并汇总；等待处理优先 | 灯的含义由用户定义，未核实内置状态引擎 |
| 设备控制 | 已核对 USB 串口驱动代码 | 官方说明 USB 控制，未确认 HID、CDC 或具体命令协议 |
| 普通 AI 网页 | 已查资料没有提供通用网页完成检测方案 | 没有自动接入承诺 |
| 对 SeeEnd 的价值 | 可研究并评估复用的软件实现 | 借鉴开放控制思路及清晰界定兼容范围 |

资料：[ClawLight CLI 仓库](https://github.com/clawlight/clawlight-cli)、[Future Device 产品页](https://futuredevice.ai/products/status-light)。

## Claw Light 的软件路径

工具产生事件 → Hook 或插件适配 → 统一状态文件 → 会话汇总 → 托盘／终端界面／USB 灯。

### 逐工具适配

官方文档列出 Claude Code、opencode、Codex CLI 与 GitHub Copilot CLI。安装器检测工具是否存在，按各工具原生扩展方式安装；卸载移除自己管理的内容。opencode 采用插件，其他相关入口使用 Hook。此处是 ClawLight 声明的适配范围，不等于 ChatGPT 网页、Codex 桌面端或任意 AI 应用兼容。具体工具版本须重新实测。

参考：[支持的工具](https://clawlight.dev/docs/supported-agents)、[适配器注册代码](https://github.com/clawlight/clawlight-cli/blob/main/src/harness.rs)。

### 事件与状态分离

hook.rs 将工具事件归一为 working、idle、needs_input、resumed、ended 等动词，再更新共享状态。Claude 路径中 Stop 映射 Inactive，SessionEnd 映射 Done。归一入口对未知动词直接忽略，并保护 NeedsHelp：普通 working 不能解除等待处理，明确 resumed 才能恢复运行。

这里的 Notification 是工具 Hook 事件名，不能理解为 macOS 或 Windows 的通用系统通知监听。

参考：[Hook 与归一事件代码](https://github.com/clawlight/clawlight-cli/blob/main/src/hook.rs)。

### 会话汇总

state.rs 按 session_id 保存状态、更新时间等信息。NeedsHelp 总是优先显示；有空闲会话时是否压过运行中会话由配置决定。Done 不参与活跃状态汇总；代码还会把进程已退出或缺少进程身份且长期陈旧的会话清理为 Done。因此这个 Done 表示会话已结束或被清理，不代表业务成功，不可直接映射 SeeEnd 的完成庆祝动画。

参考：[状态与汇总代码](https://github.com/clawlight/clawlight-cli/blob/main/src/state.rs)。

### 串口实现

led.rs 采用 115200 波特率，每次发一个 ASCII 状态字符加换行：R、Y、G、N。驱动每 500 毫秒检查状态，状态改变立即发送，并每 2 秒重复当前状态；断连后每 2 秒重新扫描。这说明不能把官网“事件驱动”描述理解为整条链路完全没有轮询。

自动发现主要检查 Espressif USB VID；该 VID 不能唯一证明设备就是 Claw Light。所查普通状态发送路径没有读取逐条执行 ACK。SeeEnd 应保留协议握手、设备唯一 ID 和应用层回执，以便多块 ESP32 共存及诊断故障。

参考：[串口驱动代码](https://github.com/clawlight/clawlight-cli/blob/main/src/led.rs)。

### 不应默认继承的行为

自动会话命名会读取首条提示并调用 Claude CLI 生成名称，这会带来模型调用及内容处理，不适合直接继承到 SeeEnd 的默认本地隐私承诺。建议默认使用用户别名或本地生成的不透明名称。

CLI 仓库 LICENSE 标注 MIT，可评估代码复用并保留所要求的版权和许可声明；这不自动覆盖另一个固件仓库、硬件设计或品牌素材。本次官方固件链接未能成功访问，不能把“官网称开源”当作已完成固件审查。

参考：[命名代码](https://github.com/clawlight/clawlight-cli/blob/main/src/hook.rs)、[CLI 许可证](https://github.com/clawlight/clawlight-cli/blob/main/LICENSE)、[硬件文档](https://clawlight.dev/docs/claw-light)。

## Future Device 已确认与未确认的部分

产品页明确说明：设备加厂商工具并非完成的 Agent 集成，用户需要自行用 Hook／脚本连接状态。官方文章描述由电脑通过 USB 控制灯。可参考的路线是让硬件控制保持简单，把来源逻辑留在上位机。

本次检查的资料没有给出可验证的 SDK／CLI 下载与参数、USB 命令帧、设备识别方式或开源状态引擎。部分官方文章还明确称控制接口尚未确认。因此不能编造其串口命令、断线恢复、ACK 或多任务机制，也不宜把它作为现成软件底座。

参考：[产品页](https://futuredevice.ai/products/status-light)、[官方状态说明](https://futuredevice.ai/blogs/news/agent-state-on-your-desk)、[接口与通知比较](https://futuredevice.ai/blogs/news/status-light-vs-desktop-notifications)。

## 对现有 PRD 的建议变更

下表为建议，尚未替换原 PRD 基线。

| 编号 | 建议 | 理由与验收方向 |
| --- | --- | --- |
| CR01 | 将原生 Hook／插件列为独立触发源 | 有明确事件的工具优先直接接入；停止、取消、等待与结束逐项测试 |
| CR02 | 增加 needs_input | 等待审批或回答时持续柔和提示；明确恢复或取消才清除，不能因其他任务工作而消失 |
| CR03 | 分开会话、任务、显示状态 | session_id 标识长会话，task_id 标识一轮任务；会话结束、失联、崩溃均不能触发成功完成动画 |
| CR04 | 增加来源新鲜度和健康字段 | unknown／disconnected 属于来源健康；与 working／done 等业务状态正交处理 |
| CR05 | 安装器支持探测与可逆配置 | 显示检测到的工具、待添加配置和兼容状态；用户选择启用，保留已有 Hook，卸载仅清理自有条目 |
| CR06 | 增加虚拟火苗与来源定位 | 没有硬件也可验证事件；从“需要你”提示打开对应任务，无法精确定位时说明降级 |
| CR07 | USB 保持简洁但保留握手与 ACK | 不必向灯具发送完整任务内容；验证错误设备拒绝、重试不重复起播与拔插恢复 |
| CR08 | 不默认引入模型命名或内容读取 | 默认使用本地别名；状态提示不应要求额外模型请求 |

建议内部模型包含 source_id、session_id、task_id、task_state、attention_required、source_health 和 display_effect。是否将 needs_input 作为第五个协议状态，需与既有四态固件兼容策略一并确定，不能只改 UI 文案。

## 实施顺序建议

1. 先完成软件虚拟火苗、本地事件入口与 USB 闭环；不依赖实际 AI 网站即可验证状态机。
2. 增加一个经过实测的原生 Hook 适配和一个浏览器网站适配，覆盖开发者与普通网页用户两类来源。
3. 增加 Windows 通知规则，提供可理解的配置、测试和失败诊断。
4. 再扩展 Wi-Fi、更多工具与移动端。原 PRD 的两网站目标如需调整，应显式更新发布范围，而不是悄悄减少承诺。

可以保留一个兼容试验：让 SeeEnd 样机临时识别 ClawLight 的 R/Y/G/N 输出，以低成本演示火苗显示。但该试验只能表达 ClawLight 的会话汇总状态，不能据此声称已经实现 SeeEnd 的任务成功判断，也不能作为正式设备协议替代方案。
