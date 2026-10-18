# 下载 SeeEnd

**[下载 SeeEnd 0.8.5 · macOS Apple 芯片](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.5/SeeEnd-0.8.5-Darwin-arm64.zip)**

**只需下载此安装包。** 已包含 App 和运行依赖，解压后启动 `SeeEnd.app` 即可。没有灯具也能使用。

[安装与帮助](https://yipengchen.github.io/SeeEnd-downloads/support.html) · [版本说明](https://github.com/YipengChen/SeeEnd-downloads/releases/tag/v0.8.5) · [隐私政策](https://yipengchen.github.io/SeeEnd-downloads/)

当前为桌面预览版，仅支持 macOS Apple 芯片，使用 ad-hoc 签名，尚未经过 Apple 公证。

<details>
<summary>第三方源码与许可材料（可选，使用 App 无需下载）</summary>

安装包已包含 Qt、PySide6 等运行依赖及许可文本。下列材料仅供查阅、修改或重新构建第三方库时使用。

[按需下载第三方源码与构建材料](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.5/SeeEnd-third-party-sources-0.8.5.zip)

第三方材料保留各自许可，不包含 SeeEnd 原创源码。GitHub 自动生成的 Source code 档案仅包含本公开仓库的文档，也无需下载即可使用 App。

[第三方许可说明](THIRD_PARTY_NOTICES.md) · [动态库替换说明](LIBRARY_REPLACEMENT.md)

第三方材料 SHA-256：`79f2a4420125ad2baec1a91f9483bda5f523be1f667a991ea3e995e568438ebf`

</details>

<details>
<summary>安装包校验与技术说明</summary>

`SeeEnd-0.8.5-Darwin-arm64.zip` SHA-256：`93c034323c1effae6d151df16b3fd4e7ba951b505385ab833d84007d461f6c63`

0.8.5 的 22 个核心模块编译为原生机器码，安装包不携带这些模块的 Python 源码或字节码。原生编译提高逆向成本，不保证无法逆向；界面、接入适配器和随包 JavaScript 不在本次核心编译范围内。

[详细测试说明](TESTING.md)

</details>

发布者：Yipeng Chen · 联系邮箱：ypchenmail@gmail.com

SeeEnd 原创代码从 0.8.4 起采用[专有许可](LICENSE)，源码仓库保持私有。本公开仓库仅提供安装包、政策、安装说明和许可材料，不含 SeeEnd 原创源码。

## 待发布版本

App 0.8.6 / 扩展 0.2.10 / Skill 1.0.1 的发布资料保存在 releases/v0.8.6/。该版本当前状态为 prepared；本地打包和文件同步不表示安装包已上传。确认资产实际可下载并核对 SHA-256 后，再提升本页与 support.html 的主下载入口。当前入口仍指向此前已发布的 0.8.5。
