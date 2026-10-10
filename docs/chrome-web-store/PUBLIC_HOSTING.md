# SeeEnd 独立资料托管

2026-10-10：App 0.8.6、开发扩展 0.2.10 与商店待提交 ZIP 已在 [公开下载仓库](https://github.com/YipengChen/SeeEnd-downloads/releases/tag/v0.8.6) 发布，四项资产完成匿名完整下载和 SHA-256 校验。Skill 1.0.1 在独立仓库发布并核验下载；源码仓库仍为私有。实际发布清单位于 downloads/releases/v0.8.6/release.json。

根 THIRD_PARTY_NOTICES 与 LIBRARY_REPLACEMENT 是参与源码指纹的构建快照，保留当时“待发布”描述；发布状态以此记录和 release.json 为准。GitHub 发布不代表 Chrome 商店提交或通过审核。

## 2026-10-09 历史记录

原始源码仓库保持私有，独立公开资料仓库为 https://github.com/YipengChen/SeeEnd-downloads 。

隐私政策已上线：https://yipengchen.github.io/SeeEnd-downloads/ ，无需登录即可访问，已填入商店后台。

发布者已确认从 SeeEnd 0.8.4 起对原创代码采用专有许可，保留此前已经授予的许可权利。公开材料可以包括安装包、隐私政策、使用说明、许可文本及第三方源码包，不能包含 SeeEnd 原创源码仓库或源码归档。

第三方源码材料包含对应版本的 Qt/PySide、LGPL 动态库的上游源码和发行方补丁，不包含 SeeEnd 原创实现。当前安装包包含运行依赖、完整许可文本和库替换说明；第三方源码与构建材料独立提供，标注为“可选，使用 App 无需下载”。历史 0.8.4 包曾内嵌源码材料，不作为当前分发方式。

0.8.4 已公开下载：https://github.com/YipengChen/SeeEnd-downloads/releases/tag/v0.8.4 。原 0.8.3 草稿资产已移除。两项新资产均通过匿名 HTTP 206 下载检查，GitHub SHA256 与本地文件一致；原始源码仓库经 API 复核仍为私有。

上述公开发布结果是 2026-10-09 当时的记录，不表示此后清理产生的本地安装包已同步上传。当前本地包已移除 AI Flame 固件和独立 esptool 工具，重新发布时需替换安装包及对应第三方材料并复核校验值。
