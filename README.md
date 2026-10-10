# SeeEnd

**中文** | [English](README.en.md)

软件名为 **SeeEnd**。当前支持 Yeelight YLTD003 屏幕挂灯 Pro 和 YLDP005 W3 彩色灯泡。

把 AI 任务状态变成一束烛火。

SeeEnd 是运行在本机的 AI 任务提醒工具。它接收命令行工具、网页对话和本地 API 的状态事件，通过屏幕烛火、桌面通知或实体灯提示任务进行、等待输入、完成与异常。**没有硬件也能使用**，首次启动默认进入虚拟模式。

当前版本：**0.8.6 桌面预览版** · Python **3.11+** · **专有许可**

[下载 SeeEnd 0.8.6 · macOS Apple 芯片](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.6/SeeEnd-0.8.6-Darwin-arm64.zip) · [SeeEnd Skill 1.0.1](https://github.com/YipengChen/SeeEnd-skill/releases/tag/v1.0.1)

## 能做什么

- **统一任务状态**：按来源记录任务生命周期，区分真实任务、模拟诊断及取消／超时。
- **多种提醒方式**：屏幕烛火、菜单栏／托盘状态、桌面通知，以及通过局域网连接的 Yeelight 灯具。桌面通知可独立于灯光工作。
- **可调提醒**：每台灯具独立设置各状态的颜色与亮度；提醒偏好统一设置持续时间和静默时段，支持活动记录 CSV 导出。
- **中英文界面**：在「帮助与诊断 → 界面语言 / Language」选择中文或 English，立即切换并保存在本机；托盘菜单和通知同步使用所选语言。
- **本地处理**：Hub 仅监听 `127.0.0.1:8765`，通过凭据鉴权；接入以任务状态元数据为目标，不保存聊天正文，无需向 SeeEnd 提供模型 API Key。

## 接入与平台范围

| 接入方式 | 当前实现 | 验证边界 |
| --- | --- | --- |
| 命令行 | Codex、Claude Code、Qoder CLI、DeepSeek Harness、Gemini CLI、OpenCode | Hook 或本地插件；按工具说明加载配置，逐工具验证新任务 |
| 网页扩展 | ChatGPT、DeepSeek、千问、豆包、元宝、Kimi、智谱清言、Claude、Gemini | 实验功能；页面结构变化可能影响识别，自动测试不替代真实账号验收 |
| macOS 桌面客户端 | 豆包、千问、元宝、ChatGPT 普通聊天 | 实验功能，默认关闭；需要系统权限，依赖客户端版本与控件结构 |
| 本地 HTTP API | 自有脚本和工具上报任务事件 | 使用独立来源凭据；接口见架构文档 |

macOS 已有实机验证记录；Windows 和 Linux 提供共享实现与原生构建流程，仍需实机验收。桌面客户端识别目前仅支持 macOS。当前版本未完成所有平台的签名、安装器和发布验收。

## 从源码启动

需要 Python 3.11 或更新版本；项目 CI 使用 3.11／3.13。先下载源码并进入本仓库目录。

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
2. 在「应用接入」选择要使用的来源：编程工具使用 SeeEnd Skill 安装接入，网页点击「安装浏览器扩展」，按引导准备并验证。
3. 在对应 AI 应用中提交新任务，观察烛火和活动记录。模拟诊断只验证事件通路。
4. 如需桌面通知，在「提醒偏好」开启并完成系统授权。

仅运行无界面 Hub 时，可安装基础依赖并使用以下入口；桌面应用已内置 Hub，通常无需另开一份：

```sh
python -m pip install -e .
python -m aiflame --hub
```

安装后的 `seeend` 和 `seeend-hub` 命令分别对应桌面与 Hub 入口；旧命令 `ai-flame`、`ai-flame-hub` 作为兼容别名保留。Python 模块名 `aiflame`、原用户数据目录、`AIFLAME_DATA_DIR` 环境变量及内部接入标识保持兼容，已有配对、偏好和任务记录可继续沿用。

详细接入步骤见[使用指南](docs/quick-start.md)和[浏览器扩展说明](aiflame/integrations/chatgpt_web/extension/README.md)。Windows 浏览器桥接需要打包版提供的 Bridge 可执行文件。

浏览器扩展在「应用接入 → 安装浏览器扩展」中按四步引导接入：准备文件、打开 Chrome 加载窗口、选择安装文件夹、连接并验证网站。验证页先选择网站并开启提醒，再在 Chrome 发送一条新提问。无需手动解压或模型 API Key；不连接灯具也可使用。引导含实际截图、路径复制和每步完成标志，分发包附有同内容的离线图文教程。

## 编程工具通过 Skill 接入

在「应用接入 → 编程工具」点击「复制安装指令」，粘贴到实际使用的工具，让它从 [SeeEnd-skill](https://github.com/YipengChen/SeeEnd-skill) 安装完整的 `seeend/` Skill 并配置本机接入。后续验证、诊断、修复、更新和移除，都在工具中通过同一 Skill 发起。

App 只保留统一 Skill 入口、各工具状态与提醒开关。保持 SeeEnd 运行，无需灯具或向 SeeEnd 提供模型 API Key。按 Skill 提示完成工具加载、Hook 信任或授权，再验证一条新任务；配置写入或模拟测试不代表真实接入成功。详见[使用指南](docs/quick-start.md#编程工具通过-skill-接入)。

## 可选硬件

支持 **Yeelight YLTD003 屏幕挂灯 Pro** 的 RGB 背光和 **Yeelight YLDP005 W3 彩色灯泡**，通过原厂局域网协议连接，无需刷写固件。先在 Yeelight App 中配网并开启局域网控制，再到「我的设备」选择型号、搜索或手动输入 IP 添加灯具。可同时连接多台灯具，每台独立保存连接信息和各状态颜色、亮度；浏览设备不会断开连接，切换到虚拟模式会暂停所有实体设备连接。

AI Flame 0.1 / 0.2 的完整实现保留在归档分支 [`archive/ai-flame-2026-10-09`](https://github.com/YipengChen/SeeEnd/tree/archive/ai-flame-2026-10-09)，主分支不再提供这两款设备、固件维护或火焰调参。升级时忽略旧灯板连接记录，保留 Yeelight 设备及任务、偏好数据。

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
python packaging/build.py --download-sources
python packaging/verify_frozen.py
python packaging/archive.py
```

默认输出到 `dist/`，macOS 应用为 `dist/SeeEnd.app`。可指定独立输出目录：

```sh
python packaging/build.py --download-sources --dist-dir dist/staging
python packaging/verify_frozen.py --dist-dir dist/staging
python packaging/archive.py --dist-dir dist/staging
```

分发时以 `SeeEnd-版本-平台-架构.zip`（Linux 为 `.tar.gz`）作为唯一 App 下载入口：其中包含应用、运行依赖、许可说明和离线安装教程，用户只需下载此包。`SeeEnd-third-party-sources-版本.zip` 单独提供第三方源码与构建材料，供查阅或修改第三方库时按需下载，不嵌入 App 压缩包。公开页面应将其标为“可选，使用 App 无需下载”，并保留材料链接。

干净源码环境首次构建使用 `--download-sources` 下载并校验第三方源码；也可先运行 `python packaging/prepare_licenses.py --download`。材料准备完成后，省略该选项即可离线重建；缺失或校验失败会在原生编译前停止。构建依赖包括 PyYAML，用于读取发行方配方。

应用与分发包包含 `SeeEnd-build.json`，记录 App/扩展版本、Git revision、工作树是否有未提交变更、源码指纹、平台、架构、Python 版本和第三方材料指纹。构建后改动源码或版本必须重新构建；验证和归档拒绝将旧 App 改名为新版本。第三方源码归档只选择清单中已校验的上游归档与配方，不收录缓存目录中的任意文件。

运行 `python packaging/extension_release.py` 可从当前源码生成按版本命名的开发加载目录、开发 ZIP、商店待提交 ZIP 和校验清单。商店 ZIP 的 manifest 位于根目录且移除 `key`；开发包保留商店公钥。脚本不会覆盖历史 `docs/chrome-web-store/extension-upload.zip` 或浏览器可能正在加载的 `dist/SeeEnd-Web-extension/`。生成待提交包不代表已更新商店。

构建需要本机 C 编译器：macOS 使用 Xcode Command Line Tools，Windows 使用 Visual Studio C++ Build Tools，Linux 使用 GCC 和当前 Python 的开发头文件。`build.py` 会用 Cython 将 `core`、`outputs`、`hub`、`devices` 和 `transports` 中的 22 个核心模块编译为当前平台的原生库，先验证所有原生模块可加载，再打包；任一步失败都会停止，不回退到 Python 字节码。源码开发仍使用 `.py` 文件，生成的 C 代码和调试文件仅保存在临时构建目录。

修改核心逻辑后，可运行 `python packaging/verify_native.py`，在移除核心 Python 文件的隔离目录中执行完整回归测试；也可用 `python packaging/build.py --verify-native --download-sources` 测试后直接打包，桌面构建 CI 使用此方式。安装包检查会确认核心模块不在 PYZ 中，且不携带其 `.py`、`.pyc` 或生成的 C 代码。原生编译提高逆向成本，不保证无法逆向；界面、接入适配器和随包 JavaScript 不在本次核心编译范围内。

GitHub Actions 对推送和 PR 运行测试；桌面构建工作流支持手动触发和 `v*` 标签触发。跨平台构建成功不代表完成实机验收。

本地受限环境若以 EPERM/EACCES 禁止回环端口或本地 IPC，原生回归会逐项列出受影响的跳过用例，冻结验证仍执行真实辅助进程的静默输入、消息帧和包内容检查，并明确报告 HTTP 生命周期未验证。其他 socket 错误会失败；CI 不允许因这些权限限制跳过集成验证。测试使用隔离配置，不操作真实设备。

应用品牌图标以 `aiflame/assets/icon.svg` 为源文件。安装桌面依赖与 Pillow 后，运行 `python packaging/render_icon.py` 同步生成 PNG、Windows ICO 和 macOS ICNS，再重新构建应用。菜单栏火苗用于显示灯光状态，与品牌图标独立。

## 项目结构

以下目录均位于本仓库内，文档统一保存在 `docs/`。

```text
aiflame/
  core/           事件、任务状态机、存储和用户数据路径
  hub/            本机 HTTP 服务与编排
  integrations/   CLI、网页、桌面客户端和本地 API 接入
  outputs/        灯光与桌面通知投递
  desktop/        PySide6 原生桌面界面
  devices/        Yeelight 发现、连接和配置
  transports/     Yeelight 局域网通信
  assets/         桌面界面资源、图标与翻译
tests/            自动测试和手动验收工具
packaging/        PyInstaller 构建与归档
setup.py          源码分发入口
docs/             使用指南、架构、历史验收及产品资料
```

- [架构与跨平台边界](docs/architecture.md)
- [历史验收记录](docs/validation.md)：记录特定版本及设备上的结果，不代表当前所有环境均已验证。
- [贡献指南](CONTRIBUTING.md)
- [第三方资源说明](THIRD_PARTY_NOTICES.md)

## 许可证

0.8.4 起，项目原创代码采用 [SeeEnd 专有软件许可](LICENSE)，源码仓库保持私有。此前版本已授予的许可权利不被撤销。第三方依赖和品牌素材保留其各自许可与权利归属，详见[第三方资源说明](THIRD_PARTY_NOTICES.md)。

### Yeelight YLTD003（Screen Light Bar Pro）

在「我的设备」选择 **Yeelight YLTD003**。先使用 Yeelight App 将灯具接入 2.4 GHz Wi-Fi，并开启「局域网控制」；电脑与灯具需在同一局域网。切换至硬件模式后，可搜索灯具并选择，或手动输入灯具 IPv4 地址连接。连接后用「试亮此设备」检查背光。此型号保留原厂程序，配网在 Yeelight App 中完成，无需 USB 或刷写固件。

仅控制后方 RGB 氛围灯，前方白光照明沿用灯具设置。以下为默认颜色，各状态的颜色与亮度均可独立调整：

| AI 状态 | 背光 |
| --- | --- |
| 工作中 | 恒定蓝色 `#4A90E2`，默认亮度 20% |
| 完成 | 恒定绿色 `#52C788`，默认亮度 50%，按提醒时长结束；可保持至用户活动 |
| 异常 / 等待输入 | 恒定橙色 `#FF9F43`，默认亮度 50%，按提醒时长结束 |
| 待机、暂停、静默、当前状态亮度为 0 | 熄灭背光 |

在「我的设备 → 对应设备」分别设置工作中、完成、异常 / 等待输入的颜色与 0–100% 亮度；仅作用于所选设备。100% 直接下发为灯具协议的亮度参数 `100`，不额外缩放，亮度上限为 100%。将某个状态设为 0% 仅关闭该状态的灯光提醒，其他状态保持各自设置。两款 Yeelight 均使用恒定颜色与设定亮度，不使用呼吸、脉动或「减少动态效果」设置。每台灯具独立连接、断开、试亮与记录回执，可与其他 Yeelight 灯具同时同步任务。重新连接时不补播旧的完成提示。

接入依据：[Yeelight 官方 LAN 协议](https://www.yeelight.com/download/Yeelight_Inter-Operation_Spec.pdf)、[官方 Chroma Connector 能力判断](https://github.com/Yeelight/Yeelight-Chroma-Connector/blob/main/ChromaBroadcastSampleApplication/ChromaBroadcastSampleApplicationDlg.cpp)、[官方产品册](https://en.yeelight.com/wp-content/uploads/sites/4/2023/08/Yeelight-Fun.pdf)。以发现响应中的 `lamp15`、物理 `id` 和 `support` 方法列表识别兼容灯具；通过 SSDP 风格 UDP `239.255.255.250:1982` 搜索（手动 IP 优先定向查询），一次局域网搜索约 4 秒，间隔 1 秒自动发送 3 轮发现查询，在同一 UDP 接收窗口合并迟到响应并按物理 ID 去重；手动 IP 查询收到有效响应后立即结束。使用 TCP `55443` 上以 CRLF 结尾的 JSON 控制。自动重连按物理 ID 重新搜索，避免 IP 变更时控制其他灯具。

使用 `bg_set_scene` 的原生有限 color flow；固件未声明此方法时回退到 `bg_set_power` + `bg_start_cf`，不发送前光命令。灯效结束使用官方 `action=2` 自动关闭背光。工作与保持提示采用最长 30 秒的原生灯效，每 20 秒续期；应用异常退出或网络中断后，最后一次成功提交的灯效会自行结束。控制命令间隔至少 1.1 秒，低于官方每连接每分钟 60 条配额；灯具的总配额为每分钟 144 条，与其他控制软件共用。此版本不做 UDP 实时逐帧同步。

实现经过模拟发现响应、TCP 回执、灯效与界面回归验证，尚未在 YLTD003 实物上验证。若搜索及手动连接均失败，请先检查局域网控制是否可用，以及路由器隔离、VPN 和 UDP 1982 / TCP 55443 的可达性。手动 IP 仍需要灯具回复发现查询以确认物理身份与能力。

### Yeelight YLDP005（W3 彩色灯泡）

在「我的设备」选择 **Yeelight YLDP005**。与 YLTD003 一样，先通过 Yeelight App 接入 2.4 GHz Wi-Fi 并开启「局域网控制」，再搜索或手动输入 IP 连接。支持独立试亮、断开、自动重连和多设备同步，灯具保留原厂固件。

YLDP005 使用整颗灯泡显示任务状态，默认颜色与亮度同上：工作中恒定蓝色（20%）、完成恒定绿色（50%）、异常 / 等待输入恒定橙色（50%），待机、暂停、静默或当前状态亮度为 0% 时熄灭。各状态的颜色与 0–100% 亮度独立设置，100% 直接下发，不额外缩放；支持提示时长和完成后保持至活动设置。连接期间灯泡由 SeeEnd 用于任务提醒。

[Yeelight 官方产品规格](https://store.yeelight.com/sv/products/yeelight-smart-bulb)确认 YLDP005 为 W3 彩色灯泡；[Home Assistant 官方设备表](https://www.home-assistant.io/integrations/yeelight/)将其 LAN 型号映射为 `colorb`。连接使用相同的 SSDP 发现和 TCP 55443 JSON 协议，但灯泡通道使用 `set_scene`、`set_power`、`start_cf` 及 `power` / `bright` / `rgb` 属性；YLTD003 的背光继续使用 `bg_*` 指令。搜索按所选型号及公布的控制能力过滤，防止两种灯具接错通道。

原生灯效续期、限速、自动熄灭与断线处理沿用上述 YLTD003 实现。已通过模拟协议及界面回归测试；已在 `colorb`、发现固件版本 `7` 的 W3 实物上验证发现与 TCP 状态查询。该实物在 TCP 建连后立即发送首条查询时，会偶发完全不回执；仅延长回执超时无效。客户端在每次建连后等待 0.5 秒再发送指令，修复后连续 5 次新连接均返回正常状态。此等待同时覆盖添加设备和自动重连，关闭应用时可中断。该实物的灯效显示、续期及熄灭仍需另行验证。
