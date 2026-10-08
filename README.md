# SeeEnd

软件暂定名为 **SeeEnd**（原 AI Flame App）；实体灯系列继续使用 **AI Flame**，当前包括 **AI Flame 0.1** 和 **AI Flame 0.2**。

把 AI 任务状态变成一束烛火。

SeeEnd 是运行在本机的 AI 任务提醒工具。它接收命令行工具、网页对话和本地 API 的状态事件，通过屏幕烛火、桌面通知或实体灯提示任务进行、等待输入、完成与异常。**没有硬件也能使用**，首次启动默认进入虚拟模式。

当前版本：**0.8.3 桌面预览版** · Python **3.11+** · **GPL-3.0-only**

## 能做什么

- **统一任务状态**：按来源记录任务生命周期，区分真实任务、模拟诊断及取消／超时。
- **多种提醒方式**：屏幕烛火、菜单栏／托盘状态、桌面通知，以及通过 USB 或 Wi-Fi 连接的实体灯。桌面通知可独立于灯光工作。
- **可调提醒**：每台设备独立设置亮度与减少动态效果；提醒偏好统一设置持续时间和静默时段，支持活动记录 CSV 导出。
- **本地处理**：Hub 仅监听 `127.0.0.1:8765`，通过凭据鉴权；接入以任务状态元数据为目标，不保存聊天正文，无需向 SeeEnd 提供模型 API Key。

## 接入与平台范围

| 接入方式 | 当前实现 | 验证边界 |
| --- | --- | --- |
| 命令行 | Codex、Claude Code、Qoder CLI、DeepSeek Harness、Gemini CLI、OpenCode | Hook 或本地插件；连接后需重启目标会话，逐工具验证 |
| 网页扩展 | ChatGPT、DeepSeek、千问、豆包、元宝、Kimi、智谱清言、Claude、Gemini | 实验功能；页面结构变化可能影响识别，自动测试不替代真实账号验收 |
| macOS 桌面客户端 | 豆包、千问、元宝、ChatGPT 普通聊天 | 实验功能，默认关闭；需要系统权限，依赖客户端版本与控件结构 |
| 本地 HTTP API | 自有脚本和工具上报任务事件 | 使用独立来源凭据；接口见架构文档 |

macOS 已有实机验证记录；Windows 和 Linux 提供共享实现与原生构建流程，仍需实机验收。桌面客户端识别目前仅支持 macOS。当前版本未完成所有平台的签名、安装器和发布验收。

## 从源码启动

需要 Python 3.11 或更新版本；项目 CI 使用 3.11／3.13。先下载源码并进入 `code/` 目录。

```sh
python -m venv .venv
```

激活环境：

```sh
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

安装并启动桌面应用：

```sh
python -m pip install -e ".[desktop]"
python -m aiflame
```

1. 保持默认的「虚拟模式」，无需购买或连接灯具。
2. 在「应用接入」连接要使用的来源。网页接入还需要加载 Chrome 扩展并连接本机桥接。
3. 在对应 AI 应用中提交新任务，观察烛火和活动记录。模拟诊断只验证事件通路。
4. 如需桌面通知，在「提醒偏好」开启并完成系统授权。

仅运行无界面 Hub 时，可安装基础依赖并使用以下入口；桌面应用已内置 Hub，通常无需另开一份：

```sh
python -m pip install -e .
python -m aiflame --hub
```

安装后的 `seeend` 和 `seeend-hub` 命令分别对应桌面与 Hub 入口；旧命令 `ai-flame`、`ai-flame-hub` 作为兼容别名保留。Python 模块名 `aiflame`、原用户数据目录、`AIFLAME_DATA_DIR` 环境变量及内部接入标识保持兼容，已有配对、偏好和任务记录可继续沿用。

详细接入步骤见[使用指南](../docs/quick-start.md)和[浏览器扩展说明](aiflame/integrations/chatgpt_web/extension/README.md)。Windows 浏览器桥接需要打包版提供的 Bridge 可执行文件。

## 编程工具的增量接入

在「应用接入」找到对应工具，展开「高级设置」，先核对实际文件及目录，再点击「连接」。六个工具分别提供自动配置说明、连接前后步骤和可复制的增量片段／插件命令，详见[使用指南](../docs/quick-start.md#编程工具配置文件与增量接入)。

「检查接入」会区分具体配置错误、来源未开启、配置就绪但未验证、任务进行中，以及已收到同一真实任务开始与完成。失败项会给出文件或事件名称和下一步；模拟测试与历史安装结果不会代替本次真实验证。

## 可选硬件

当前灯光程序版本为 **0.4.8**，支持以下两款 **ESP32-S3-N16R8 + IS31FL3731 暖黄色矩阵灯板**：

| 硬件型号 | LED 阵列 |
| --- | --- |
| AI Flame 0.1 | 9×16 |
| AI Flame 0.2 | 7×15 |

另支持 **Yeelight YLTD003 屏幕挂灯 Pro** 的 RGB 背光和 **Yeelight YLDP005 W3 彩色灯泡**，通过原厂局域网协议连接，无需刷写固件。配网和灯效说明见下方 [YLTD003 接入说明](#yeelight-yltd003screen-light-bar-pro)。

点击「我的设备」先选择大图标中的型号，再选择已配对的具体设备，或「添加此型号设备」。可同时连接多个型号及同型号的多台设备，每台独立保存配对、USB 和网络信息。浏览型号或设备不会断开连接；AI 状态会同步发送到所有已连接设备。断开、更新或维护只影响所选设备，其他设备继续显示状态；切换到全局「虚拟模式」会暂停所有实体设备连接。使用 GPIO21 / GPIO18 作为 SDA / SCL；板型、接线与安装步骤见[硬件使用说明](../docs/quick-start.md#9×16-暖黄色矩阵火苗)。

切换到「硬件模式」后，在「我的设备」选择型号后，通过 USB 安装对应灯光程序、配对或配置 2.4 GHz Wi-Fi。项目包含指定板型的 MicroPython 底层固件；初始化会擦除开发板数据，日常灯光更新使用单独的更新入口。请勿将内置固件用于其他 Flash／PSRAM 规格的开发板。

## 火焰实时调参

启动源码 Hub 后打开 [火焰实验室](http://127.0.0.1:8765/tuning)，或在 macOS 双击项目中的 `启动火焰调参.command`。旧打包版不包含此网页，需先退出旧版再启动。

1. 在「我的设备」连接灯板，并通过 USB「更新灯光程序」至 0.4.8 或更新版本（保留配对与 Wi-Fi）。
2. 进入调参页，选择设备，点击「开始实时调参」。支持 USB 和 Wi-Fi。
3. 调整 15 项核心参数：高度、宽度、尖端收束、火芯强度、总亮度、边缘柔化、亮度闪烁、整体速度、运动幅度、慢摆幅度、慢摆频率、尖端抖动、高度起伏、阵风强度、碎焰强度。其余参数使用内部默认值；导入旧方案时保留其完整参数。拖动约每 180 毫秒合并下发，显示「硬件已确认」时表示板上首帧已成功写入。
4. 可保存到当前浏览器，或导入／导出 JSON。保存方案用于再次调试，不会覆盖日常任务默认灯效。
5. 点击「停止调参」恢复任务灯效。关闭页面或停止续租约 6 秒后自动退出；主机断线时板上有独立超时熄灭保护。

调参暂时覆盖所选灯板的任务灯效及亮度／减少动态／静默偏好，不影响其他灯板。亮度仍限制为 0–25%，PWM 上限为 16。两款暖黄色单色灯板不支持色相调节。

## 开发与测试

JavaScript 测试需要 Node.js（构建 CI 使用 22）和 npm；应用运行本身不依赖 Node.js。

```sh
python -m unittest discover -s tests -v
npm ci --ignore-scripts
npm test
```

无显示器的环境可设置 `QT_QPA_PLATFORM=offscreen`。Python 集成测试会创建本机回环端口和本地 IPC，需要运行环境允许这些操作。未安装桌面依赖时，部分 Qt 测试会跳过。

`tests/verify_*_live.py`、`tests/verify_live.py` 是手动实机验收工具，不属于上述自动测试；运行前检查其参数及目标环境，部分脚本会调用真实 AI 工具或设备。

## 构建桌面应用

在目标操作系统上构建：

```sh
python -m pip install -e ".[desktop,build]"
python packaging/build.py
python packaging/archive.py
```

默认输出到 `dist/`，macOS 应用为 `dist/SeeEnd.app`。可指定独立输出目录：

```sh
python packaging/build.py --dist-dir dist/staging
python packaging/archive.py --dist-dir dist/staging
```

GitHub Actions 对推送和 PR 运行测试；桌面构建工作流支持手动触发和 `v*` 标签触发。跨平台构建成功不代表完成实机验收。

应用品牌图标以 `aiflame/assets/icon.svg` 为源文件。安装桌面依赖与 Pillow 后，运行 `python packaging/render_icon.py` 同步生成 PNG、Windows ICO 和 macOS ICNS，再重新构建应用。菜单栏火苗用于显示灯光状态，与品牌图标独立。

## 项目结构

以下运行、测试和构建目录位于 `code/`，文档位于仓库根目录的 `docs/`。

```text
aiflame/
  core/           事件、任务状态机、存储和用户数据路径
  hub/            本机 HTTP 服务与编排
  integrations/   CLI、网页、桌面客户端和本地 API 接入
  outputs/        灯光与桌面通知投递
  desktop/        PySide6 原生桌面界面
  devices/        设备维护与 ESP32 固件
  transports/     USB / Wi-Fi 通信
  assets/         界面资源与 Web 控制台
tests/            自动测试和手动验收工具
packaging/        PyInstaller 构建与归档
setup.py          源码包文档收集与链接适配
hardware/         灯板调试工具
../docs/          使用指南、架构、历史验收及产品资料
```

- [架构与跨平台边界](../docs/architecture.md)
- [历史验收记录](../docs/validation.md)：记录特定版本及设备上的结果，不代表当前所有环境均已验证。
- [贡献指南](CONTRIBUTING.md)
- [第三方资源说明](THIRD_PARTY_NOTICES.md)

## 许可证

项目原创代码采用 [GNU GPL v3.0](LICENSE)（`GPL-3.0-only`）。第三方固件、依赖和品牌素材保留其各自许可与权利归属，详见[第三方资源说明](THIRD_PARTY_NOTICES.md)。

### Yeelight YLTD003（Screen Light Bar Pro）

在「我的设备」选择 **Yeelight YLTD003**。先使用 Yeelight App 将灯具接入 2.4 GHz Wi-Fi，并开启「局域网控制」；电脑与灯具需在同一局域网。切换至硬件模式后，可搜索灯具并选择，或手动输入灯具 IPv4 地址连接。连接后用「试亮此设备」检查背光。此型号保留原厂程序，配网在 Yeelight App 中完成，无需 USB 或刷写固件。

第一版只控制后方 RGB 氛围灯，前方白光照明沿用灯具设置：

| AI 状态 | 背光 |
| --- | --- |
| 工作中 | 蓝色 `#4A90E2` 缓慢呼吸，亮度为偏好值的约 60% |
| 完成 | 绿色 `#52C788` 缓慢呼吸，按提醒时长结束；可保持至用户活动 |
| 异常 / 等待输入 | 橙色 `#FF9F43` 柔和脉动，按提醒时长结束 |
| 待机、暂停、静默、亮度为 0 | 熄灭背光 |

在「我的设备 → 对应设备 → 灯光表现」设置 0–25% 亮度与「减少动态效果」（恒定颜色）；仅作用于所选设备。每台灯具独立连接、断开、试亮与记录回执，可与 AI Flame 灯板同时同步任务。重新连接时不补播旧的完成提示。

接入依据：[Yeelight 官方 LAN 协议](https://www.yeelight.com/download/Yeelight_Inter-Operation_Spec.pdf)、[官方 Chroma Connector 能力判断](https://github.com/Yeelight/Yeelight-Chroma-Connector/blob/main/ChromaBroadcastSampleApplication/ChromaBroadcastSampleApplicationDlg.cpp)、[官方产品册](https://en.yeelight.com/wp-content/uploads/sites/4/2023/08/Yeelight-Fun.pdf)。以发现响应中的 `lamp15`、物理 `id` 和 `support` 方法列表识别兼容灯具；通过 SSDP 风格 UDP `239.255.255.250:1982` 搜索（手动 IP 优先定向查询），使用 TCP `55443` 上以 CRLF 结尾的 JSON 控制。自动重连按物理 ID 重新搜索，避免 IP 变更时控制其他灯具。

使用 `bg_set_scene` 的原生有限 color flow；固件未声明此方法时回退到 `bg_set_power` + `bg_start_cf`，不发送前光命令。灯效结束使用官方 `action=2` 自动关闭背光。工作与保持提示采用最长 30 秒的原生灯效，每 20 秒续期；应用异常退出或网络中断后，最后一次成功提交的灯效会自行结束。控制命令间隔至少 1.1 秒，低于官方每连接每分钟 60 条配额；灯具的总配额为每分钟 144 条，与其他控制软件共用。此版本不做 UDP 实时逐帧同步。

实现经过模拟发现响应、TCP 回执、灯效与界面回归验证，尚未在 YLTD003 实物上验证。若搜索及手动连接均失败，请先检查局域网控制是否可用，以及路由器隔离、VPN 和 UDP 1982 / TCP 55443 的可达性。手动 IP 仍需要灯具回复发现查询以确认物理身份与能力。

### Yeelight YLDP005（W3 彩色灯泡）

在「我的设备」选择 **Yeelight YLDP005**。与 YLTD003 一样，先通过 Yeelight App 接入 2.4 GHz Wi-Fi 并开启「局域网控制」，再搜索或手动输入 IP 连接。支持独立试亮、断开、自动重连和多设备同步，不安装 AI Flame 固件。

YLDP005 使用整颗灯泡显示任务状态：工作中蓝色低亮呼吸、完成绿色呼吸、异常 / 等待输入橙色脉动，待机或静默时熄灭。沿用 0–25% 亮度、提示时长、减弱动效和完成后保持至活动设置；连接期间灯泡由 SeeEnd 用于任务提醒。

[Yeelight 官方产品规格](https://store.yeelight.com/sv/products/yeelight-smart-bulb)确认 YLDP005 为 W3 彩色灯泡；[Home Assistant 官方设备表](https://www.home-assistant.io/integrations/yeelight/)将其 LAN 型号映射为 `colorb`。连接使用相同的 SSDP 发现和 TCP 55443 JSON 协议，但灯泡通道使用 `set_scene`、`set_power`、`start_cf` 及 `power` / `bright` / `rgb` 属性；YLTD003 的背光继续使用 `bg_*` 指令。搜索按所选型号及公布的控制能力过滤，防止两种灯具接错通道。

原生灯效续期、限速、自动熄灭与断线处理沿用上述 YLTD003 实现。已通过模拟协议及界面回归测试，尚未在 YLDP005 实物上验证。
