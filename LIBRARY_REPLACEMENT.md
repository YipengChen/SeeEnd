# 替换 LGPL 动态库

SeeEnd 当前原创代码采用 GPL-3.0-only；本说明同时保留第三方 LGPL 库的替换与调试权利。兼容的动态库可独立替换，应用对应源码另随同版本发行提供。

正常使用 App 只需下载桌面安装包。下述第三方源码与构建材料仅在查阅、修改或重新构建第三方库时按需下载。

先读取随包 `SeeEnd-build.json` 的 `app_version`，选择同版本的 `SeeEnd-third-party-sources-<app_version>-<platform>-<architecture>.zip`。本地构建的材料与安装包由 `packaging/archive.py` 生成在同一分发目录；公开下载入口见 [SeeEnd Releases](https://github.com/YipengChen/SeeEnd/releases)。本地构建不代表该版本已经上传。

## macOS Apple 芯片包

1. 保留原安装包，在自己可写的目录复制 SeeEnd.app。退出正在运行的应用。
2. Qt 库通常位于 SeeEnd.app/Contents/Frameworks/ 的 libQt6*.dylib；PySide6 和 Shiboken 扩展位于同目录的 PySide6/、shiboken6/。Resources/ 中可能有指向这些文件的链接，应保留目录和链接结构。
3. 从同版本第三方源码与构建材料中解压 Qt、pyside-setup 及对应发行方 recipe，具体依赖版本见随包 `third_party_licenses/MANIFEST.json`。使用其构建脚本和补丁构建修改后的库。采用兼容的架构、Python ABI、文件名、安装名和公共接口；架构和 Python 版本以 `SeeEnd-build.json` 的 `architecture`、`python` 为准。
4. 将修改后的兼容动态库放到原库位置。若其 install name 与原包不同，使用 macOS install_name_tool 调整为相同的相对加载路径；可用 otool -L 检查。
5. macOS 要求改动后的代码签名有效。对自己修改的应用重新进行本地签名，例如 codesign --force --deep --sign - SeeEnd.app，然后运行该副本。此操作不需要 SeeEnd 私有签名密钥；本预览包使用 ad-hoc 签名，没有强制库哈希校验。

启动失败时检查架构、Python ABI、库依赖和签名信息。该流程允许修改和运行 LGPL 库，但不保证任意接口不兼容的库均可工作。

## Windows / Linux

使用与应用 Python ABI、架构和动态加载路径兼容的 LGPL DLL／共享库替换对应文件。选择对应平台的构建包及匹配材料；各目标系统的安装、系统权限与真实设备行为须分别验证。

第三方源码 ZIP 仅含第三方源码和发行方补丁；SeeEnd 完整对应源码为单独的 SeeEnd-source-<app_version>.zip。联系方式：ypchenmail@gmail.com。
