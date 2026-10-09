# Yeelight Cube Lite 接入 SeeEnd 的证据与实物验证

调研日期：2026 年 10 月 9 日。

Cube Lite 已有针对型号的原生 LAN 控制代码，支持 20×5 RGB 矩阵的逐点控制，可作为 SeeEnd 任务状态灯的适配候选。当前 SeeEnd 尚未注册或适配此灯具；正式支持仍需新增驱动并完成实物验证。

## 官方产品与型号核对

[易来官方产品页](https://store.yeelight.com/products/yeelight-smart-lamp-cube-lite)列出像素 DIY、文字、表情、时钟、灯效与 Matter 支持。这些是产品功能说明，不能据此认定所有功能都通过 Matter 或公开 LAN 协议开放。

目标为一体式 Cube Smart Lamp Lite，不是可拼接的 Cube Matrix、Panel 或 Spot。购买时核对外形、铭牌和地区版本；销售资料常见型号为 YLFWD-0062，但实际型号、固件和协议型号以实物为准。[型号参考](https://synergy21.de/Yeelight-Cube-Lite/248409)。

## SignalRGB 原生 UDP 证据

来源：[SignalRGB 官方 Yeelight 插件](https://gitlab.com/signalrgb/yeelight)、[源码浏览页](https://gitlab.com/signalrgb/yeelight/-/blob/main/Yeelight.js)、[原始源码](https://gitlab.com/signalrgb/yeelight/-/raw/main/Yeelight.js)。以下行号对应调研时读取的 main 分支，后续可能变化。

| 源码位置 | 内容 |
| --- | --- |
| 264–280 行 | 将 CubeLite 映射为 Cube Lite |
| 338–349 行 | 启用逐 LED 控制，默认数量和上限均为 100 |
| 471–489 行 | udp_sess_new 建立会话并读取 token；udp_sess_keep_alive 保活 |
| 490–515 行 | 实现开关、亮度与整体颜色命令 |
| 516–527 行 | activate_fx_mode 进入 direct 模式；update_leds 发送逐点颜色 |
| 539–545 行 | 直接向设备 IP 的 UDP 55444 发包 |
| 685–708 行 | 解析发现响应及能力，包含 CubeLite 分支 |

这提供了 Cube Lite 原生 UDP 接入及逐点显示的实现依据。具体固件能否稳定使用、支持哪些命令，需逐项实测。插件 README 说明其面向 UDP 兼容设备，不控制 TCP 设备。

## 独立 TCP 控制与像素布局证据

[danielp370-msft 的 Cube Lite 控制项目](https://github.com/danielp370-msft/yeelight-cube)明确描述：

- TCP 连接设备的 55443 端口。
- 使用 set_power 开灯，activate_fx_mode 切换 direct 模式。
- 使用 update_leds 发送 Base64 编码的逐点 RGB 数据。
- 矩阵为 20 列、5 行；索引 0 在左下角，逐行从左向右、从下向上，最上行为 80–99。
- 连接断开后，显示内容保留。

项目提供 Python 控制程序、文字及图案示例。其布局和断开行为属于作者设备的记录，后续仍应核对购买设备的方向与固件表现。

## 专门的 Home Assistant 本地控制项目

[Max-src 的 Cube Lite 集成](https://github.com/Max-src/yeelight-cube-lite)提供矩阵绘制、文字、渐变、原生时钟、灯效及音乐模式，并说明通信在局域网内完成。配网流程要求使用 Yeelight Station，为灯具加入 2.4 GHz Wi-Fi 后开启 LAN Control；支持设备自动发现和手动 IP。

[cube_matrix.py 通信源码](https://github.com/Max-src/yeelight-cube-lite/blob/main/custom_components/yeelight_cube/cube_matrix.py)实际使用 TCP socket 与 Yeelight 库，包含逐点发送、连接恢复、串行化及限流逻辑。可核对的位置：

- 21–56 行：Cube Lite 客户端与持久 TCP socket 状态。
- 182 行起：连接恢复与设备可达性检查。
- 622–700 行附近：direct 模式切换时的连接处理。
- 770–804 行附近：update_leds 发送路径与命令恢复逻辑。

逐点快速发送采用不等待回执的路径，因此不能直接将发送成功视为灯具已确认显示。源码中的限流注释和常量也存在不一致，不能当作可靠的性能规格照搬。

这些项目是第三方实现证据，不是厂商对所有地区版本与固件的兼容保证，也不意味着 SeeEnd 必须依赖 Home Assistant 才能控制灯具。

## SeeEnd 现状与适配范围

本次读取的本地实现：

- `aiflame/devices/models.py`：仅注册 Yeelight YLTD003 与 YLDP005，未注册 Cube Lite。
- `aiflame/devices/yeelight.py`：按型号过滤发现响应，固定 TCP 55443，并要求 get_prop、对应通道 set_power 和 start_cf 能力。
- `aiflame/transports/yeelight.py`：使用有限 color flow 显示任务颜色；未实现 direct 模式和 update_leds。

因此，当前 SeeEnd 不能直接通过现有设备选项连接 Cube Lite；仅增加名称不足以完成接入。

需要增加型号识别与能力检查、矩阵布局、逐点控制及设备连接恢复，并将任务状态映射为适合 20×5 的内容。当前两款受支持灯具只显示整体任务颜色；Cube Lite 的图案和文字需要新增矩阵渲染，不能复用现有整体颜色驱动。

| SeeEnd 用途 | 实现判断 |
| --- | --- |
| 工作、完成、异常的整面颜色 | 有接口基础 |
| 勾号、感叹号、等待图标 | 可由逐点绘制实现 |
| 进度条与多区域任务状态 | 可由矩阵布局实现 |
| 数字或短英文 | 有第三方显示示例 |
| 中文任务名称 | 5 行高度限制字形，需简化或滚动后实测 |
| 呼吸、闪烁与动画 | 有接口基础，持续刷新性能待验证 |

以上属于基于接口的开发判断，并非 SeeEnd 已实现功能。TCP 和 UDP 均有参考实现，首选路径应由实测稳定性、回执及退出恢复行为决定。

## 已知稳定性线索

[易来论坛的 Cube Lite 用户反馈](https://forum.yeelight.com/t/topic/36739)记录了 SignalRGB 能接入同步、但数分钟后卡死的情况。这是个别使用记录，未建立统一的型号、固件、供电或刷新频率对照，不能推断所有设备都会出现。

Max-src 项目 README 也记录了供电不足时冻结或掉网，并提供刷新和恢复措施。实测时需要分别排查供电不足、刷新过快、并发连接和固件差异，不能将每次卡死都归因于同一种原因。

SeeEnd 当前 Yeelight 灯效依靠有限时长自动结束；Cube Lite 的 direct 显示可能在断线后保留。适配时必须明确退出、断网和异常终止后的行为，避免残留已过期的“正在工作”状态。

## 到货验证清单

1. 核对型号与地区版本，记录初始固件、App 版本、设备 ID 和 IP。
2. 使用对应 App 配网，记录 LAN Control 开关是否存在及是否开启。
3. 保存发现响应与完整 support 列表，核对协议型号是否为 CubeLite。
4. 分别验证 TCP 55443 和 UDP 55444，记录连接、会话、回执与超时表现。
5. 低频测试开关、亮度和整体颜色；测试期间让其他灯光控制软件退出。
6. 进入 direct 模式，以低亮单点逐索引测试 100 点布局、RGB 顺序和物理方向。
7. 显示工作、完成、异常及待机图案，检查状态切换、黑屏和恢复原显示。
8. 先验证低频事件驱动显示，再逐步提高动画刷新频率；记录长时间运行和失败恢复情况，不预设可稳定达到的帧率。
9. 在供电充足条件下进行数小时测试；必要时对照不同电源和亮度，分离供电问题与通信问题。
10. 测试断外网、断 Wi-Fi、IP 变化、应用退出及异常终止，检查旧内容是否保留、是否可恢复或关闭。
11. 按实际需求补充验证时钟、原生灯效、音乐模式及与 Matter 同时使用的冲突。

## 实测记录

| 项目 | 待填写结果 |
| --- | --- |
| 铭牌型号、地区版本、固件 | |
| App 版本与 LAN Control | |
| 协议型号、物理 ID、support | |
| 电源、线材与测试亮度 | |
| TCP 55443 及回执表现 | |
| UDP 55444 会话与保活 | |
| 100 点布局与 RGB 顺序 | |
| 四态图案与状态切换 | |
| 稳定刷新频率与运行时长 | |
| 断网、重连与 IP 变化 | |
| 退出、异常终止与旧状态清理 | |
| 原生灯效与 Matter 冲突 | |

正式接入的验收条件：身份识别可靠，任务状态显示正确，持续运行稳定，回执语义准确，掉线和退出后行为可预期。
