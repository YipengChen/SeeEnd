# SeeEnd 许可与第三方组件

当前 SeeEnd 原创代码、Skill 与项目文档采用根目录 LICENSE 中的 GNU GPL v3.0（GPL-3.0-only）。Copyright © 2026 Yipeng Chen，联系邮箱 ypchenmail@gmail.com。旧发行版保留发行时条款及已经授予的权利。第三方组件和商标不因项目采用 GPL 而被重新授权。

## Qt、PySide6 与动态库

本机 macOS Apple 芯片构建使用 Qt、PySide6 和 Shiboken 6.11.0 的动态库，按 LGPL-3.0 使用。Qt Core、GUI、Widgets、Network、DBus 来自 qtbase，SVG 来自 qtsvg；应用不使用 Qt PDF 或 Qt WebEngine。Anaconda 构建所用补丁和构建配方与相应上游源码一并提供。

应用保留 Qt/PySide 的动态链接方式，没有应用层库哈希锁定。用户可以依 LGPL 修改和替换相应库，并为调试这些修改进行必要的逆向工程。第三方组件保留各自许可权利。

- 第三方许可文本、版权信息和构建版本：应用资源目录的 third_party_licenses/。
- 对应第三方源码及上游构建补丁：使用 `SeeEnd-build.json` 的 `app_version` 选择同版本 `SeeEnd-third-party-sources-<app_version>.zip`。本地归档与安装包位于同一分发目录；已发布材料见 [SeeEnd Releases](https://github.com/YipengChen/SeeEnd/releases)。该 ZIP 仅含第三方材料；SeeEnd 对应源码作为独立源码 ZIP 与同版本二进制一同提供。本地生成不表示已经上传。
- 替换库与安装说明：[LIBRARY_REPLACEMENT.md](LIBRARY_REPLACEMENT.md)。

**使用 SeeEnd 只需下载桌面安装包。** 安装包已包含运行所需的动态库及其许可文本；第三方源码与构建材料单独提供，供查阅、修改或重新构建第三方库时使用，无需下载即可使用 App。下载地址不可用时，可通过 ypchenmail@gmail.com 获取相应材料。

## 其他依赖

米家扫码登录与 MIoT 云请求协议适配参考 Xiaomi-cloud-tokens-extractor（Copyright © 2020 Piotr Machowski，MIT）。完整许可见随包 Xiaomi-cloud-tokens-extractor-MIT.txt。此功能为兼容实现，不代表小米官方接入或背书。

miIO 加密使用 cryptography（Apache-2.0 / BSD-3-Clause），及其运行依赖 cffi、pycparser；随包保留相应许可文本。macOS 设备 Token 使用本机 AES-256-GCM 文件加密，密钥单独保存在同一私有数据目录；旧系统钥匙串记录仅作迁移。持有密文和密钥仍可解密。Windows 设备 Token 使用当前用户 DPAPI；微信凭据由独立存储处理。

微信绑定二维码在本机使用 python-qrcode 8.2 编码（BSD / MIT；Copyright © 2011 Lincoln Loop，含 Kazuhiko Arase 原始实现），完整版权与许可随包保留。微信 iLink 协议兼容实现参考 Tencent/openclaw-weixin 公开协议文档；SeeEnd 不包含 OpenClaw 运行时，也不代表腾讯官方客户端或背书。

CPython 使用 PSF；PyObjC 使用 MIT；PyInstaller bootloader 按其运行例外分发冻结应用。系统动态库各自保留原有版权和许可证。随包 third_party_licenses/MANIFEST.json 记录实际材料版本和来源；各目录保留完整许可文本。

核心 Python 模块使用 Cython 3.3.0 编译为原生动态库。Cython 使用 Apache-2.0，其上游许可声明编译输出不属于 Cython 的衍生作品；编译不改变 SeeEnd 原创代码的许可。安装包内保留 Cython 许可，上游材料提供在单独的第三方源码包中，SeeEnd 原创 Python 源码、构建脚本与配置另见同版本源码 ZIP；生成的 C 文件可从该源码重新生成。

libiconv、gettext/libintl、GLib 等 LGPL 动态库如被构建收录，其对应上游源码和发行方构建补丁也提供在第三方源码包中。SeeEnd 未自行修改这些库。

## 图标和浏览器资源

SeeEnd 原创浏览器 JavaScript 同样采用 GPL-3.0-only。

AI 平台和设备名称、标识用于说明兼容对象，不表示官方隶属或背书。第三方商标及图标保留其权利归属，不由 SeeEnd 的 GPL 许可重新授权。品牌图标来源记录在 `aiflame/assets/brands/sources.json`，来源记录不等于权利方授予再分发许可；衍生发行方应自行确认其使用符合权利方要求。

## 音效资源

`aiflame/assets/sounds/cat.wav` 来源于真实小猫录音，原作者 nathan-osman，作品「meow-1.wav」，使用 CC0 1.0 Universal Public Domain Dedication。

- 原作品与许可：https://freesound.org/people/nathan-osman/sounds/686775/
- 许可全文：https://creativecommons.org/publicdomain/zero/1.0/legalcode
- 原作品的公开预览：https://cdn.freesound.org/previews/686/686775_2956624-lq.mp3

SeeEnd 将公开预览解码、合并单声道、以 1.18 倍速率提亮音高并输出为 24 kHz / 16-bit PCM WAV，峰值归一化到 0.43，添加 5 毫秒淡入与 8 毫秒淡出。柔和铃声和清脆提示为 SeeEnd 原创合成。

## 原始政策和源码

- Qt LGPL 说明：https://www.qt.io/development/open-source-lgpl-obligations
- Qt/PySide 官方源码：https://download.qt.io/official_releases/
