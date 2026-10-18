# 替换 LGPL 动态库

SeeEnd 原创代码保持专有许可；本说明保障第三方 LGPL 库的替换与调试权利。无需取得 SeeEnd 原创源码即可替换兼容的动态库。

正常使用 App 只需下载桌面安装包。下述第三方源码与构建材料仅在查阅、修改或重新构建第三方库时按需下载。

## macOS Apple 芯片包

1. 保留原安装包，在自己可写的目录复制 SeeEnd.app。退出正在运行的应用。
2. Qt 库通常位于 SeeEnd.app/Contents/Frameworks/ 的 libQt6*.dylib；PySide6 和 Shiboken 扩展位于同目录的 PySide6/、shiboken6/。Resources/ 中可能有指向这些文件的链接，应保留目录和链接结构。
3. 从单独提供的[第三方源码与构建材料](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.5/SeeEnd-third-party-sources-0.8.5.zip)中解压 Qt 6.11.0、pyside-setup 6.11.0 及对应发行方 recipe。使用其构建脚本和补丁构建修改后的库。采用兼容的 arm64 架构、CPython 3.14 ABI、文件名、安装名和公共接口。
4. 将修改后的兼容动态库放到原库位置。若其 install name 与原包不同，使用 macOS install_name_tool 调整为相同的相对加载路径；可用 otool -L 检查。
5. macOS 要求改动后的代码签名有效。对自己修改的应用重新进行本地签名，例如 codesign --force --deep --sign - SeeEnd.app，然后运行该副本。此操作不需要 SeeEnd 私有签名密钥；本预览包使用 ad-hoc 签名，没有强制库哈希校验。

启动失败时检查架构、Python ABI、库依赖和签名信息。该流程允许修改和运行 LGPL 库，但不保证任意接口不兼容的库均可工作。

## Windows / Linux

使用与应用 Python ABI、架构和动态加载路径兼容的 LGPL DLL／共享库替换对应文件。当前公开预览产物仅验证 macOS Apple 芯片版本；其他平台构建仍需本机验证。

源码包仅含第三方源码和第三方发行方补丁。联系方式：ypchenmail@gmail.com。
