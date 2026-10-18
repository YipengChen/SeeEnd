# SeeEnd 0.8.56

2026-10-16 已发布，四个分发包及独立 Skill 安装包均通过匿名下载和 SHA-256 校验。

配套版本：App **0.8.56**、浏览器扩展 **0.2.13**、Skill **1.0.3**。App 仅提供 macOS Apple 芯片预览包，使用 ad-hoc 签名，未经过 Apple 公证。

[下载 App](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.56/SeeEnd-0.8.56-Darwin-arm64.zip) · [开发扩展](https://github.com/YipengChen/SeeEnd-downloads/releases/download/v0.8.56/SeeEnd-Web-extension-0.2.13.zip) · [Skill 1.0.3](https://github.com/YipengChen/SeeEnd-skill/releases/tag/v1.0.3)

本版修复 Gemini 明确结束生成、但缺少本轮成功操作栏时长期显示进行中的路径：持续确认 5 秒后转为「状态未知」，不把中断或结果未确认当成成功。正常回复仍通过本轮有效操作栏确认完成。Skill 的入口、参考文档及契约快照统一对齐 App 0.8.56，事件协议和管理契约未变。

安装只需 App ZIP；解压启动 SeeEnd.app，在「应用接入」完成连接，更新扩展后刷新网页。第三方源码包仅供查阅或重新构建依赖，无需下载即可使用 App。

[构建身份](SeeEnd-build.json) · [完整资产清单](release.json) · [SHA-256](SHA256SUMS) · [验证范围](TESTING.md) · [安装与帮助](../../support.html)

Chrome 商店后台已核对 0.2.13 待审核、已发布 0.2.12，不公开分发。本次执行过新版上传，未点击提请审核；随后复核时后台已显示待审核。GitHub 分发包与商店审核状态分别记录。
