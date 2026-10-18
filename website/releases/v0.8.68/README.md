# SeeEnd 0.8.68

配套 App **0.8.68**、浏览器扩展 **0.2.14**、Skill **1.0.5**。本目录保存发布资料；公开状态以[发布状态](../../release-status.json)为准。

[macOS Apple 芯片](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.68/SeeEnd-0.8.68-Darwin-arm64.zip) · [Windows x64](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.68/SeeEnd-0.8.68-Windows-AMD64.zip) · [Skill 1.0.5](https://github.com/YipengChen/SeeEnd-skill/releases/tag/v1.0.5)

本版加入音效提醒，改进设备连接、扩展安装引导和投递结果说明，并修复界面快照落后时漏投递系统通知与音效的问题。管理客户端升级为双向签名鉴权，配套使用 Skill 1.0.5；该 Skill 同时修复多设备或较多记录造成的大状态响应读取失败。

macOS 使用 ad-hoc 签名，未经过 Developer ID 签名和 Apple 公证。Windows 包已在原生 Windows runner 完成构建、冻结验证和真实 EXE 窗口启动检查；用户电脑的浏览器、系统通知及设备联动仍需实机确认。

只需下载对应平台 App ZIP。macOS 解压后启动 SeeEnd.app；Windows 须保留完整 SeeEnd 文件夹及 _internal，启动 SeeEnd.exe。在“应用接入”完成连接，更新扩展后刷新网页并用新任务验证。

第三方源码包只供查阅和重新构建依赖，使用 App 无需下载。Windows 与 macOS 的第三方材料分开发布，不能相互替代；文件名详见资产清单。

[验证范围](TESTING.md) · [macOS 构建身份](SeeEnd-build-macOS-arm64.json) · [Windows 构建身份](SeeEnd-build-Windows-AMD64.json) · [完整资产清单](release.json) · [SHA-256](SHA256SUMS) · [安装与帮助](../../support.html)

GitHub 扩展包发布不表示 Chrome Web Store 已同步上传、审核或发布。
