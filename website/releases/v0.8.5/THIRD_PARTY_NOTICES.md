# SeeEnd 许可与第三方组件

SeeEnd 0.8.4 起的原创代码采用 LICENSE 中的专有许可，版权所有者为 Yipeng Chen，联系邮箱 ypchenmail@gmail.com。此变更不撤销此前版本已经授予的许可权利。专有许可不覆盖第三方组件，也不限制其许可证授予的权利。

## Qt、PySide6 与动态库

本机 macOS Apple 芯片构建使用 Qt、PySide6 和 Shiboken 6.11.0 的动态库，按 LGPL-3.0 使用。Qt Core、GUI、Widgets、Network、DBus 来自 qtbase，SVG 来自 qtsvg；应用不使用 Qt PDF 或 Qt WebEngine。Anaconda 构建所用补丁和构建配方与相应上游源码一并提供。

应用保留 Qt/PySide 的动态链接方式，没有应用层库哈希锁定。用户可以依 LGPL 修改和替换相应库，并为调试这些修改进行必要的逆向工程。专有许可中的限制不适用于这些权利。

- 第三方许可文本、版权信息和构建版本：应用资源目录的 third_party_licenses/。
- 本版本第三方源码及上游构建补丁：[SeeEnd-third-party-sources-0.8.5.zip](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.5/SeeEnd-third-party-sources-0.8.5.zip)。仅含第三方材料，不含 SeeEnd 原创源码。
- 替换库与安装说明：[LIBRARY_REPLACEMENT.md](LIBRARY_REPLACEMENT.md)。

**使用 SeeEnd 只需下载桌面安装包。** 安装包已包含运行所需的动态库及其许可文本；第三方源码与构建材料单独提供，供查阅、修改或重新构建第三方库时使用，无需下载即可使用 App。下载地址不可用时，可通过 ypchenmail@gmail.com 获取相应材料。

## 其他依赖

CPython 使用 PSF；PyObjC 使用 MIT；PyInstaller bootloader 按其运行例外用于专有应用。系统动态库各自保留原有版权和许可证。随包 third_party_licenses/MANIFEST.json 记录实际材料版本和来源；各目录保留完整许可文本。

核心 Python 模块使用 Cython 3.3.0 编译为原生动态库。Cython 使用 Apache-2.0，其上游许可声明编译输出不属于 Cython 的衍生作品；编译不改变 SeeEnd 原创代码的许可。安装包内保留 Cython 许可，上游材料提供在单独的第三方源码包中，不包含 SeeEnd 原创源码或生成的 C 代码。

libiconv、gettext/libintl、GLib 等 LGPL 动态库如被构建收录，其对应上游源码和发行方构建补丁也提供在第三方源码包中。SeeEnd 未自行修改这些库。

## 图标和浏览器资源

SeeEnd 浏览器 JavaScript 为运行功能随包提供，不构成对原创代码的开源或再分发授权。

AI 平台和设备名称、标识用于说明兼容对象，不表示官方隶属或背书。第三方商标及图标保留其权利归属，不由 SeeEnd 专有许可重新授权。

## 原始政策和源码

- Qt LGPL 说明：https://www.qt.io/development/open-source-lgpl-obligations
- Qt/PySide 官方源码：https://download.qt.io/official_releases/
