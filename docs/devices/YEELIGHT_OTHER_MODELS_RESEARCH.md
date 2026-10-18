# 其他 Yeelight 灯具接入 SeeEnd 的候选与证据

> 历史调研快照（2026-10-09）：以下“当前”与“尚未支持”描述均指当时。0.8.55 已注册屏幕挂灯、W3、Beam、Cube Lite 及两代米家插座；当前实现见 产品基线（历史本地材料，已归档）。本文保留来源与待验清单，不代表本轮重新联网核验或实体设备验收。

调研日期：2026 年 10 月 9 日。

范围排除 SeeEnd 已支持的 YLTD003 屏幕挂灯 Pro、YLDP005 W3 彩色灯泡，以及已单独调研的 Beam YLFWD-0021 和 Cube Lite。优先筛选能通过局域网显示任务颜色、图标或分区内容的设备。

以下是适配候选，不代表当前 SeeEnd 已支持，也不保证所有地区版本及固件一致。优先方向为灯带 1S、彩色灯泡 1S、D2 和 Cube Matrix；分区灯效可进一步验证 Obsid RGBIC 与灯带 Pro。

## 候选型号

| 灯具与型号 | 已找到的接入证据 | SeeEnd 用途 | 适配判断 |
| --- | --- | --- | --- |
| 彩光灯带 1S，YLDD05YL | HA 已测试 LAN 设备，协议型号 strip6 | 屏幕后方、桌边统一任务颜色 | 优先，接近现有整灯驱动 |
| 彩色灯泡 1S，YLDP13YL；另有 YLDP13AYL | HA 已测试，协议型号分别为 color4、color6 | 搭配灯罩显示任务状态 | 优先，可能复用 W3 控制逻辑 |
| GU10 W1 彩色版，YLDP004-A | HA 已测试，协议型号 colorc | 定向照亮墙面或桌面 | 有较强接入依据，需要 GU10 灯座 |
| D2 桌面或床头灯，YLCT01YL | 官方 Station 支持列表包含 D2，配套流程要求开启 LAN Control | 柔和的独立桌面提醒 | 值得验证，协议型号及完整方法待实测 |
| LED Light Strip Pro，YLDD005 | 官方支持 Station、可寻址 RGB 和游戏同步 | 多区域提示与动态灯效 | 有 LAN 使用依据，分区接口需进一步核对 |
| Cube Matrix、Panel、Spot | SignalRGB 源码包含 CubeMatrix、CubePanel、CubeSpot 的 UDP 适配 | Matrix 图标、Panel 色块、Spot 投光 | Matrix 优先，需要新的矩阵驱动 |
| Obsid RGBIC Light Strip，协议型号 Chameleon2 | SignalRGB 源码明确适配，并启用逐 LED 控制 | 多任务分区、进度和追光 | 接口证据强，需要 UDP 驱动及点位映射 |
| Smart LED Light Panels，YLFWD-012 | 官方 Station FAQ 列为支持设备，官方产品册列型号 | 墙面大面积提醒 | 候选，完整分区接口证据尚不足 |

Obsid 与可拼接 Cube 的采购 SKU 需进一步核对；协议型号不是铭牌型号。Cube Matrix、Panel、Spot 与 Cube Lite 是不同产品。

## 来源与证据范围

### Home Assistant 的已测试设备表

[Yeelight 官方集成文档](https://www.home-assistant.io/integrations/yeelight/)列出灯带 1S、彩色灯泡 1S 和 GU10 W1 彩色版的型号及协议型号映射，并说明需开启 LAN Control。该集成为本地控制提供实际接入依据，但测试列表不能保证购买设备的最新固件保持相同行为。

文档提供颜色、亮度及 color flow 等控制入口；具体设备可用方法应读取发现响应中的 support 列表，再逐项测试。

### 易来 Station 的官方 LAN 配置与支持列表

[Station 官方 FAQ](https://japan.yeelight.com/yeelight-station-faq.html)说明需在 Yeelight App 开启 LAN Control，并在同一局域网添加设备。支持列表包含灯带 Pro、灯带 1S、彩色灯泡 1S、GU10 W1 彩色版、D2 和 Smart LED Panels。

这证明有官方局域网使用途径，但不能直接推导各型号使用哪一种 TCP 或 UDP 协议、是否支持逐 LED、是否支持有限灯效及全部预设模式。

[Station 官方产品列表](https://en.yeelight.com/yeelight-station/)可作为交叉参考。

### 官方产品与型号资料

- [2023 年 Yeelight Fun 产品册](https://en.yeelight.com/wp-content/uploads/sites/4/2023/08/Yeelight-Fun.pdf)：灯带 1S 为 YLDD05YL，灯带 Pro 标准包为 YLDD005，GU10 W1 彩色版为 YLDP004-A。
- [D2 官方说明书](https://yeelight.de/media/akeneo_connector/media_files/Y/e/Yeelight_led_bedside_lamp_d2_user_manual_web_3c5a.pdf)：型号 YLCT01YL，具备 Wi-Fi 与 WRGB 光源。
- [2022 年官方产品册](https://en.yeelight.com/wp-content/uploads/sites/4/2022/06/Yeelight-product-brochure-2022-5-31.pdf)：Smart LED Light Panels 标注 YLFWD-012。
- [Cube 官方产品页](https://store.yeelight.com/products/yeelight-smart-cube)：提供 Matrix、Panel、Spot，以及带底座和扩展模块选项。购买时需要控制底座，扩展模块不能独立联网。

产品资料确认硬件与功能，不能替代控制协议证据。GU10 彩色版 YLDP004-A 不应与白光调光版 YLDP004 混用。

### SignalRGB 的 Cube 与 Obsid 通信实现

[插件仓库](https://gitlab.com/signalrgb/yeelight)、[源码浏览页](https://gitlab.com/signalrgb/yeelight/-/blob/main/Yeelight.js)、[原始源码](https://gitlab.com/signalrgb/yeelight/-/raw/main/Yeelight.js)。

调研时 main 分支中，264–280 行将 CubeMatrix、CubePanel、CubeSpot 和 Chameleon2 映射为对应产品；299–337 行和 351–363 行给出设备控制配置。代码使用 UDP 55444，会话建立、保活、direct 模式和 update_leds 提供逐点控制路径。

源码中的逻辑点配置不能直接视为所有灯具的实际物理灯珠排列。Panel 和 Spot 的可见显示效果也不能仅根据逐点能力标志判断，需实物核对。行号会随仓库更新变化。

## 与 SeeEnd 的适配关系

SeeEnd 现有 Yeelight 实现位于 `aiflame/devices/models.py`、`aiflame/devices/yeelight.py` 和 `aiflame/transports/yeelight.py`。目前注册 YLTD003 与 YLDP005，使用 TCP 55443、型号与能力过滤、有限 color flow 及按物理 ID 重连。

灯带 1S、彩色灯泡 1S 和 GU10 W1 彩色版有望复用部分现有控制，但必须新增型号注册，并确认 get_prop、set_power、start_cf 等方法和灯效结束动作。D2 也需先取得完整发现响应，不能仅靠商品名称适配。

Cube 与 Obsid 需要新增 UDP 会话管理、逐点数据编码、布局映射及退出恢复。它们可与 Beam、Cube Lite 共用部分通信逻辑，但设备识别、点位布局和固件行为需分别验证。

灯带 Pro 和 LED Panels 的完整分区接口应专项调研，当前不宜直接宣称可复用某个逐点协议。

## 优先级

1. **低成本任务颜色提醒**：YLDD05YL 灯带 1S 或 YLDP13YL 彩色灯泡 1S。重点验证现有驱动可复用程度及有限灯效的结束行为。
2. **独立桌面提醒物**：D2 YLCT01YL。具有柔和灯体，但需要确认当前 LAN 开关与持续运行稳定性。
3. **图标和多任务显示**：Cube Matrix 带底座套装。需要设计矩阵布局，Panel 更适合色块，Spot 更适合投光。
4. **分区灯效**：Obsid RGBIC。先确认逻辑点数、长度、索引与稳定刷新，再设计任务区域。

若再选两类设备做验证，建议 YLDD05YL 灯带 1S 与 Cube Matrix 带底座套装，分别覆盖整灯颜色控制和矩阵表达能力。这个顺序是基于 SeeEnd 需求的工程判断，不是已完成实测的采购保证。

## 稳定性线索与其他设备

[D2 用户实测反馈](https://forum.yeelight.com/t/topic/35675)记录冻结及掉网情况，并提到 LAN Control 开关。这是个别使用记录，不能推断所有固件都会出现；到货后应在供电可靠条件下做持续运行测试。

HA 的测试表还包括旧彩色灯泡 YLDP02YL、YLDP03YL、YLDP06YL，以及灯带 YLDD01YL、YLDD02YL、YLDD04YL。已有设备时值得验证；它们与上述候选的显示用途重叠，不优先新购。

吸顶灯和吊灯也有 LAN 记录，例如 YLXD02YL、YLDL01YL、YLXD013-B、YLXD013-C。RGB 氛围通道及独立控制仍需逐型号确认，且提醒会影响房间照明，优先级较低。

W4、W4 Lite、Curtain Lights、RGBIC Basic Strip 和其他新款，本次没有取得足够明确的具体型号加原生接口实现证据，暂不列为优先采购候选。此处代表证据不足，不代表已证实不支持。

Yeelight Pro 涉及网关及另一套接入体系，不能直接套用现有 TCP 驱动。[官方说明](https://en.yeelight.com/news-events/yeelight-pro-supports-matter/)确认可通过网关升级支持 Matter，但不能据此认定各灯具直接开放传统 LAN API。

## 采购与到货验证

- 核对铭牌、地区版本、实际配网 App 及固件版本；询问当前版本是否仍能开启 LAN Control。
- 确保买到完整控制套装：Cube 需底座，灯带需控制盒与电源；GU10 需兼容灯座。
- 保存设备发现响应，记录物理 ID、协议型号、固件和 support 列表。
- 先验证低频开关、颜色、亮度及任务四态，再测试分区或动画；不预设可稳定达到的帧率。
- 检查有限灯效能否自动结束，以及断网、程序退出和异常终止后是否保留旧状态。
- 检查 IP 变化后的重连、其他控制软件并发、供电与持续运行稳定性。

## 实测记录

| 项目 | 待填写结果 |
| --- | --- |
| 商品名、铭牌型号与地区版本 | |
| 控制盒或底座型号 | |
| App、固件与 LAN 开关 | |
| 物理 ID、协议型号与 support | |
| TCP 或 UDP 接口及回执 | |
| 颜色、亮度与四态提醒 | |
| 有限灯效及结束动作 | |
| 逻辑点数、分区与索引 | |
| 持续运行及稳定刷新频率 | |
| 掉线、退出与旧状态清理 | |
| SeeEnd 适配结论 | |
