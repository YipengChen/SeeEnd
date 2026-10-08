# 第三方资源说明

根目录的 GPL-3.0-only 许可适用于本项目原创代码，不替代第三方材料的原有许可。

## 内置 MicroPython 固件

`aiflame/devices/firmware/base/` 包含指定 ESP32-S3 板型的 MicroPython 固件、原始许可和来源清单：

- [许可证](aiflame/devices/firmware/base/LICENSE)
- [版本、下载来源、文件大小及 SHA-256](aiflame/devices/firmware/base/manifest.json)

这些文件是开发板初始化功能的运行资源，不应作为构建缓存删除。

## 品牌图标

`aiflame/assets/web/brands/` 中的品牌图标用于标识接入来源，已有来源记录见 [sources.json](aiflame/assets/web/brands/sources.json)。部分图标提取自本地安装的客户端，部分来自网站图标资源。

来源记录不等同于再分发授权。本项目不主张这些品牌素材或商标的所有权，也不通过 GPL 对其重新授权；公开分发前仍需由维护者确认素材的使用与再分发条件，或替换为获准素材。

## 软件依赖

Python 依赖及版本见 `pyproject.toml`；JavaScript 开发测试依赖见 `package.json` 和 `package-lock.json`。依赖各自的版权与许可证继续适用，项目根许可证不覆盖它们。

桌面独立包会包含 Python、Qt/PySide6、设备工具及其传递依赖。发布构建需保留随依赖提供的许可与版权说明；本文是资源说明，不是完整的依赖许可证清单或发布合规验收记录。

## 本地素材

原始参考视频与硬件历史备份不属于运行依赖。原始视频目录已从版本控制中排除；仓库中的历史截图与演示用于说明开发记录，不代表真实 AI 平台或硬件的完整验收。
