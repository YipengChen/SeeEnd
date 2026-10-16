# 易来 YLFWD-0021 Beam 塔灯局域网控制证据与实物验证

> 历史调研快照（2026-10-09）：以下“当前”与“尚未支持”描述均指当时。0.8.55 已注册屏幕挂灯、W3、Beam、Cube Lite 及两代米家插座；当前实现见 [产品基线](docs/product/CURRENT_PRODUCT.md)。本文保留来源与待验清单，不代表本轮重新联网核验或实体设备验收。

调研日期：2026 年 10 月 9 日。目标设备为双塔形 Yeelight Beam RGBIC Light Bar，型号 YLFWD-0021，计划购入后验证其原生局域网接口，作为 SeeEnd 灯光接入的候选设备。

公开资料已经提供针对 Beam 的原生 LAN 控制证据，尤其是 SignalRGB 的 UDP 逐灯点实现。可以据此开展接入验证；具体固件兼容性、灯点排列和稳定刷新频率仍需实物测试。

## 产品身份

[Beam 说明书](https://dwn.alza.cz/manual/129843)第 6 页标注 YLFWD-0021、2.4 GHz Wi-Fi 和 Bluetooth BLE；第 5 页描述扫描控制盒上的 Matter 二维码配网。网上也出现将 YLFWD-0021 标为 Libra2 屏幕挂灯的资料，后续应以购买页面、产品外形和实物铭牌共同核对，避免混用其他灯具资料。

## 厂商针对 Beam 的明确声明

易来于 2024 年 7 月 23 日发布新闻稿，点名 Cube、Beam RGBIC Light Bar、Obsid，说明这些产品通过 Wi-Fi 和 LAN control 实时映射电视屏幕颜色。这是 Beam 存在局域网控制功能的厂商证据，但新闻稿没有给出命令格式或固件范围。

来源：[Yeelight Enhances Home Entertainment at Samsung China Media Event](https://www.prnewswire.com/news-releases/yeelight-enhances-home-entertainment-at-samsung-china-media-event-302204720.html)。

易来于 2024 年 4 月宣布 SignalRGB 合作时也明确列出 Beam。SignalRGB 的设备列表将 Beam 列在 WiFi 设备类别下，两方资料相互印证其第三方接入能力。

来源：[易来 SignalRGB 合作公告](https://en.prnasia.com/releases/global/yeelight-elevates-rgb-experience-with-signalrgb-integration-444058.shtml)、[SignalRGB 官方支持设备列表](https://signalrgb.com/devices/)。

## SignalRGB 原生 UDP 实现

源码来源：[Yeelight.js 浏览页](https://gitlab.com/signalrgb/yeelight/-/blob/main/Yeelight.js)、[Yeelight.js 原始文件](https://gitlab.com/signalrgb/yeelight/-/raw/main/Yeelight.js)。以下行号对应调研时读取的 main 分支，后续可能变化。

| 源码位置 | 可核对的证据 |
| --- | --- |
| 264–280 行 | 将协议型号 RaysLight 映射为 Beam RGBIC Lightbar |
| 364–375 行 | Beam 启用逐 LED 控制，默认数量和上限均为 168 个逻辑灯点 |
| 471–489 行 | udp_sess_new 建立会话，解析返回 token；udp_sess_keep_alive 保活 |
| 490–515 行 | 实现 set_power、set_bright、set_rgb |
| 516–527 行 | activate_fx_mode 切换 direct 模式；update_leds 发送逐 LED 颜色 |
| 539–545 行 | 向控制器 IP 的 UDP 55444 端口发包 |
| 229–254 行 | RGB 颜色使用 Base64 字符表编码 |
| 648–649、685–703 行 | 使用 M-SEARCH 发现设备，并解析型号和 support 方法列表 |

通信代码直接使用 UDP socket，并非调用 Matter 或厂商云 API。168 是插件配置的逻辑点数，不能据此认定物理灯珠数量或两塔各分配 84 点。源码中的通用命令实现也需在实际 Beam 固件上逐项验证。

[插件 README](https://gitlab.com/signalrgb/yeelight)明确定位于 UDP 兼容设备，并说明该插件不控制 TCP 设备。因此，UDP 实现不能证明 Beam 同时开放 TCP 55443。

## 官方公开协议资料

[易来 Chroma Connector 官方仓库](https://github.com/Yeelight/Yeelight-Chroma-Connector)公开连接器代码及两份协议资料：

- [传统 LAN TCP 协议 PDF](https://www.yeelight.com/download/Yeelight_Inter-Operation_Spec.pdf)
- [UDP R2 协议文件](https://github.com/Yeelight/Yeelight-Chroma-Connector/blob/main/Yeelight_Inter_Operation_Spec_UDP_R2_en.pdf)

这些资料可作为通信机制参考。仓库文件列表与 README 已核对；本次未成功读取 UDP PDF 正文，也没有从这个仓库确认针对 Beam 的完整命令清单。开发时应结合 SignalRGB 的 Beam 适配代码和实物返回能力，不要直接假设传统 TCP 协议全部适用。

## 与 Matter 控制的关系

Matter 为基础本地控制提供标准途径。针对 Beam 的公开实物评测显示，Apple 家庭和 Google Home 可控制整套开关、亮度和单一颜色，但未开放两塔独立控制、分区控制或内置动画选择。

来源：[Beam 实物评测](https://inteligentnydom.co/recenzje/yeelight-beam-rgbic-light-bar-recenzja-oswietlenia-dekoracyjnego-matter/)。这是评测所用固件的表现，不是最新固件的完整能力保证。

对于 SeeEnd 的左右屏幕取色和分区灯效，原生 UDP 的逐灯点接口更值得优先验证。

## 到货后的验证步骤

1. **记录身份与版本**：拍摄铭牌，记录型号、地区版本、App 名称和版本、固件版本、设备 IP；同时保留初始固件版本，便于解释后续差异。
2. **启用 LAN 控制**：配网后查看 Yeelight 或 Station 设备设置中的局域网控制选项，记录是否存在和是否已开启。电脑和灯具放在可互通的同一局域网。
3. **验证发现能力**：发送 Yeelight M-SEARCH，保存完整响应，包括 model、id、Location、fw_ver、support。核对是否返回 RaysLight 和 update_leds。
4. **验证 UDP 会话**：向设备 IP 的 UDP 55444 发送 udp_sess_new，记录响应和会话 token；按插件实现发送保活。使用 session token，不预设需要提取米家账号 token。
5. **验证基础命令**：低频测试开关、亮度和整体颜色，记录成功响应与实际灯光变化。
6. **验证逐灯点命令**：切换 direct 模式，用 update_leds 发送编码颜色。先发送全暗及单个低亮灯点，再移动索引；以 168 作为插件提供的测试起点。
7. **建立物理布局**：逐点记录左右塔、前后面、底座的索引和方向。确认哪些灯点可以独立改变，以及哪些输出存在镜像或联动。
8. **验证动态表现**：从低刷新频率开始，逐步测试 10、20、30 帧每秒，记录延迟、丢帧、卡死、重连和持续运行情况；这些是测试档位，不是已确认性能。
9. **验证离线与退出行为**：完成配网后断外网保留 LAN，检查控制是否继续工作；停止保活并退出程序，检查是否恢复原灯效，必要时查明恢复方式。
10. **补充验证 TCP 与灯效**：单独检查 TCP 55443 是否开放，读取基础属性；内置动画、音乐模式和其他接口仅在有能力声明或明确资料时测试。

## 实测记录

| 项目 | 待填写结果 |
| --- | --- |
| 铭牌型号与地区版本 | |
| App 与固件版本 | |
| LAN 控制开关 | |
| 发现响应型号与 support | |
| UDP 55444 会话与保活 | |
| 基础开关、亮度、颜色 | |
| direct 模式与 update_leds | |
| 实际逻辑灯点数量 | |
| 左右塔及各面索引布局 | |
| 稳定刷新频率与持续运行时间 | |
| 断外网控制 | |
| 会话退出与灯效恢复 | |
| TCP 55443 | |
| 内置灯效调用 | |

验证成功后，优先考虑以原生 UDP 实现 SeeEnd 接入；验收条件是设备识别稳定、左右区域可独立取色、持续刷新可靠，并能在退出或掉线后恢复可预期的灯光状态。
