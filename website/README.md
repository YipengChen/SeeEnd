# SeeEnd 政策与历史下载资料

本目录已并入 SeeEnd 单仓库。后续版本由[同仓库 GitHub Actions](https://github.com/YipengChen/SeeEnd/actions)构建并在 [SeeEnd Releases](https://github.com/YipengChen/SeeEnd/releases)准备草稿，源码采用 [GPL-3.0-only](../LICENSE)。仓库目前保持私有；以下 **0.8.68 是旧仓库已经发布的历史版本**，不代表新版本已经发布。旧链接和摘要保留用于核验，历史发行许可见各版本目录。


旧仓库发布记录：App **0.8.68** · 浏览器扩展 **0.2.14** · Skill **1.0.5**。

**[下载 macOS Apple 芯片版](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.68/SeeEnd-0.8.68-Darwin-arm64.zip)** · **[下载 Windows x64 版](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.68/SeeEnd-0.8.68-Windows-AMD64.zip)**

只需下载对应平台的 App ZIP，运行依赖已包含，没有灯具也能使用。macOS 解压后启动 SeeEnd.app；Windows 解压后保留完整 SeeEnd 文件夹和 _internal，启动 SeeEnd.exe。

[安装与帮助](https://yipengchen.github.io/SeeEnd-downloads/support.html) · [版本说明](https://github.com/YipengChen/SeeEnd-downloads/releases/tag/v0.8.68) · [隐私政策](https://yipengchen.github.io/SeeEnd-downloads/)

当前为桌面预览版。macOS 使用 ad-hoc 签名，未经过 Developer ID 签名或 Apple 公证；Windows 已完成云端原生 EXE 启动检查，实际电脑的浏览器、系统通知和设备联动仍需验收。

本版加入音效提醒，改进设备连接、扩展安装引导和投递结果说明，修复界面刷新滞后时漏投递通知与音效的问题。管理客户端改为双向签名鉴权，请同步更新 Skill 1.0.5。

<details>
<summary>浏览器扩展与编程工具 Skill</summary>

安装包内已包含浏览器扩展，通常按 App 的“安装浏览器扩展”两步引导操作，无需单独下载。

[开发扩展 0.2.14 ZIP](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.68/SeeEnd-Web-extension-0.2.14.zip) · [SeeEnd Skill 1.0.5](https://github.com/YipengChen/SeeEnd-skill/releases/tag/v1.0.5)

GitHub 分发包和 Chrome Web Store 的上传、审核及发布分别记录；这里的扩展版本不代表商店已经同步更新。

</details>

<details>
<summary>第三方源码与许可材料（使用 App 无需下载）</summary>

[macOS 第三方源码与构建材料](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.68/SeeEnd-third-party-sources-0.8.68.zip) · [Windows 第三方源码与构建材料](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.68/SeeEnd-third-party-sources-0.8.68-Windows-AMD64.zip)

材料保留各自许可，不含 SeeEnd 原创源码。两个平台的材料不同，请选择匹配平台；GitHub 自动生成的 Source code 档案只包含本下载仓库的文档。

[第三方许可说明](../THIRD_PARTY_NOTICES.md) · [动态库替换说明](../LIBRARY_REPLACEMENT.md)

</details>

<details>
<summary>安装包校验与验证范围</summary>

macOS App SHA-256：`4676cb0a6b165e35f5e3f78f80e74675fdfeca525e936295ca888a833ac049a3`

Windows App SHA-256：`2ec7275fde0b453cd956a0b6d46815efea14f9d9792d267c6e38d59a3d94e6d2`

36 个核心模块编译为原生机器码，安装包不携带这些模块的 Python 源码或字节码。原生编译提高逆向成本，不保证无法逆向；界面、接入适配器和随包 JavaScript 不在核心编译范围内。

[完整 SHA-256 清单](releases/v0.8.68/SHA256SUMS) · [验证范围](releases/v0.8.68/TESTING.md) · [构建身份与资产清单](releases/v0.8.68/release.json)

</details>

发布者：Yipeng Chen · 联系邮箱：ypchenmail@gmail.com

0.8.68 等旧发行版保留发行时专有许可；当前主仓库源码采用 GPL-3.0-only，见根目录 LICENSE。

[0.8.68 发布资料](releases/v0.8.68/README.md) · [历史 0.8.56](releases/v0.8.56/README.md) · [历史 0.8.6](https://github.com/YipengChen/SeeEnd-downloads/releases/tag/v0.8.6)
