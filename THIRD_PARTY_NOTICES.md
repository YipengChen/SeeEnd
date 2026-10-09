# SeeEnd 许可与第三方组件

SeeEnd 0.8.4 起的原创代码采用 LICENSE 中的专有许可，版权所有者为 Yipeng Chen，联系邮箱 ypchenmail@gmail.com。此变更不撤销此前版本已经授予的许可权利。专有许可不覆盖第三方组件，也不限制其许可证授予的权利。

## Qt、PySide6 与动态库

本机 macOS Apple 芯片构建使用 Qt、PySide6 和 Shiboken 6.11.0 的动态库，按 LGPL-3.0 使用。Qt Core、GUI、Widgets、Network、DBus 来自 qtbase，SVG 来自 qtsvg；应用不使用 Qt PDF 或 Qt WebEngine。Anaconda 构建所用补丁和构建配方与相应上游源码一并提供。

应用保留 Qt/PySide 的动态链接方式，没有应用层库哈希锁定。用户可以依 LGPL 修改和替换相应库，并为调试这些修改进行必要的逆向工程。专有许可中的限制不适用于这些权利。

- 第三方许可文本、版权信息和构建版本：应用资源目录的 third_party_licenses/。
- 本版本第三方源码及上游构建补丁：[SeeEnd-third-party-sources-0.8.4.zip](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.4/SeeEnd-third-party-sources-0.8.4.zip)。仅含第三方材料，不含 SeeEnd 原创源码。
- 替换库与安装说明：[LIBRARY_REPLACEMENT.md](LIBRARY_REPLACEMENT.md)。

第三方源码包也随桌面分发压缩包提供；网络地址不可用时可使用随包副本。

## 独立 esptool 工具

esptool 5.4.0 使用 GPL-2.0-or-later。它作为独立程序位于 third_party/esptool/，通过普通命令行参数和标准输出工作，不收进 SeeEnd 的 Python 模块档案，不导入 SeeEnd 模块。工具未修改，上游对应源码及独立工具构建配方包含在第三方源码包中。GPL 工具自身的权利不受 SeeEnd 专有许可限制。

## 其他依赖

mpremote 使用 MIT，pyserial 使用 BSD；CPython 使用 PSF；PyObjC 使用 MIT；PyInstaller bootloader 按其运行例外用于专有应用。esptool 的第三方依赖以及系统动态库各自保留原有版权和许可证。随包 third_party_licenses/MANIFEST.json 记录实际材料版本和来源；各目录保留完整许可文本。

libiconv、gettext/libintl、GLib 等 LGPL 动态库如被构建收录，其对应上游源码和发行方构建补丁也提供在第三方源码包中。SeeEnd 未自行修改这些库。

## 设备固件和图标

内置 MicroPython ESP32-S3 固件保留 aiflame/devices/firmware/base/LICENSE 及 manifest.json 中的来源与版本。SeeEnd 配套设备脚本和浏览器 JavaScript 为运行功能随包提供，不构成对原创代码的开源或再分发授权。

AI 平台和设备名称、标识用于说明兼容对象，不表示官方隶属或背书。第三方商标及图标保留其权利归属，不由 SeeEnd 专有许可重新授权。

## 原始政策和源码

- Qt LGPL 说明：https://www.qt.io/development/open-source-lgpl-obligations
- Qt/PySide 官方源码：https://download.qt.io/official_releases/
- esptool：https://github.com/espressif/esptool
