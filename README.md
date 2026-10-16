# SeeEnd

**中文** | [English](README.en.md)

把 AI 任务状态变成一束烛火。

SeeEnd 是本机 AI 任务提醒应用：接收编程工具、网页对话和本地 API 的状态事件，用桌面火焰、系统通知、微信通知或实体设备提示工作中、等待输入和已完成。无需硬件或向 SeeEnd 提供模型 API Key。

当前源码基线：**App 0.8.55** · 浏览器扩展 **0.2.12** · Python **3.11+** · [专有许可](LICENSE)。源码版本与本机构建不代表已经公开发布；分发与商店状态见[仓库与发布说明](docs/REPOSITORIES.md)和[扩展申请进度](docs/chrome-web-store/APPLICATION_STATUS.md)。

## 产品与支持范围

- 四种提醒通道独立开关，可同时使用。首次启动或重置后只开启桌面火焰；系统通知需授权，微信需扫码绑定并准备好连接，实体设备需另行连接。
- 总览、活动记录和 macOS 系统通知可返回有定位信息的任务；双击总览或桌面火焰返回当前提醒任务。支持的终端、授权要求和定位限制见[返回任务](docs/quick-start.md#返回对应任务)。
- 每台设备独立保存状态颜色、亮度、白灯或通断电设置；活动记录可导出 CSV。界面支持中文和 English。
- Hub 默认监听 `127.0.0.1:8765`，使用凭据鉴权。保存任务生命周期元数据，不保存提问或回答正文；开启微信后会向腾讯服务发送来源、状态和时间。

| 接入 | 当前实现 |
| --- | --- |
| 编程工具 | Codex、Claude Code、Qoder CLI、DeepSeek Harness、Gemini CLI、OpenCode；通过 [SeeEnd Skill](https://github.com/YipengChen/SeeEnd-skill) 安装和维护 |
| 网页扩展 | ChatGPT、DeepSeek、千问、豆包、腾讯元宝、Kimi、智谱清言、Claude、Gemini；实验功能，网站更新后需复验 |
| 本地 HTTP API | 自有脚本或工具通过独立来源凭据上报任务事件 |

| 可选设备 | 提醒能力 |
| --- | --- |
| Yeelight YLTD003 屏幕挂灯 Pro | 背部彩灯或前部白灯 |
| Yeelight YLDP005 W3 彩色灯泡 | 彩灯或白灯 |
| Yeelight YLFWD-0021 Beam 塔灯 | 双塔彩灯或 RGB 模拟白灯 |
| Yeelight Cube Lite | 像素状态牌、彗星巡航、贪吃蛇、文字状态，另有整屏彩灯和白灯 |
| 米家智能插座 3（ZNCZ301KK）、4（ZNCZ401KK） | 控制所接灯具通断电 |

设备使用原厂局域网协议；配网、默认参数、连接及故障边界统一见[使用指南](docs/quick-start.md#连接设备)。AI Flame 旧灯板与固件不属于当前产品，旧实现保存在[归档分支](https://github.com/YipengChen/SeeEnd/tree/archive/ai-flame-2026-10-09)。

macOS 有历史实机记录；Windows 和 Linux 提供共享实现与原生构建流程，需分别验收。自动测试、设备协议回执和发送成功均不能证明用户已看到通知或真实灯光；证据边界见[验证说明](docs/validation.md)。

## 启动与接入

分发版先将 App 放到固定位置，再打开「应用接入」：编程工具复制 Skill 安装指令；网页按「安装浏览器扩展」引导操作。完成后提交一条新任务并核对开始与完成。完整步骤见[使用指南](docs/quick-start.md)。

从源码启动：

```sh
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[desktop]"
python -m aiflame
```

仅运行无界面 Hub：安装基础依赖 `python -m pip install -e .`，运行 `python -m aiflame --hub`。桌面 App 已包含 Hub，通常无需另开一份。

安装后的 `seeend` / `seeend-hub` 对应桌面与 Hub；`ai-flame` / `ai-flame-hub` 为兼容别名。`aiflame` 包名、原用户数据目录、`AIFLAME_DATA_DIR` 及内部接入标识继续兼容已有配置。

## 开发、测试与构建

Python CI 使用 3.11 / 3.13；浏览器测试另需 Node.js（构建 CI 使用 22）和 npm。App 运行本身不需要 Node.js。

```sh
python -m unittest discover -s tests -v
npm ci --ignore-scripts
npm test
```

无显示器可设置 `QT_QPA_PLATFORM=offscreen`。部分测试需要回环端口、IPC 或桌面依赖；冻结 Helper 测试需相应产物。`tests/verify_*_live.py` 是手动实机工具，运行前检查目标和参数，不属于常规自动回归。

在目标操作系统上按顺序构建、冻结验证、归档：

```sh
python -m pip install -e ".[desktop,build]"
python packaging/build.py --download-sources
python packaging/verify_frozen.py
python packaging/archive.py
```

默认输出 `dist/SeeEnd.app` 和对应分发包；三个命令可统一加 `--dist-dir dist/staging` 使用独立目录。首次构建下载并校验第三方材料，准备完成后可省略 `--download-sources` 离线重建。需要本机 C 编译器；核心模块经 Cython 原生编译，无 Python 回退。修改核心逻辑可先运行 `python packaging/verify_native.py`，或构建时加 `--verify-native`。

`SeeEnd-build.json` 记录版本、源码指纹、Git 状态与第三方材料身份；源码变更后须重建，冻结验证与归档拒绝复用不匹配的 App。分发 App ZIP（Linux 为 `.tar.gz`）是用户安装入口；第三方源码 ZIP 独立按需下载，使用 App 无需下载。当前机器的签名与发布验收状态应以实际产物为准。

`python packaging/extension_release.py` 生成版本化开发包、商店待提交 ZIP 和校验清单；生成材料不等于已提交或已发布。品牌图标以 `aiflame/assets/icon.svg` 为源，运行 `python packaging/render_icon.py` 更新派生资源后重建。

## 文档导航与许可

- [使用指南](docs/quick-start.md)：提醒、接入、任务返回与设备操作
- [当前产品基线](docs/product/CURRENT_PRODUCT.md)：已实现范围与兼容边界
- [架构与目录](docs/architecture.md)：模块、数据与协议
- [验证说明](docs/validation.md)：检查入口、证据层级与历史记录
- [协作与发布](docs/REPOSITORIES.md) · [贡献指南](CONTRIBUTING.md)
- [第三方资源说明](THIRD_PARTY_NOTICES.md) · [第三方库替换说明](LIBRARY_REPLACEMENT.md)

0.8.4 起原创实现采用 [SeeEnd 专有软件许可](LICENSE)，此前已授予的许可权利不撤销。第三方依赖和品牌素材保留各自许可与权利归属。
