# SeeEnd 0.8.6 发布资料

状态：**published**。App 0.8.6 / 扩展 0.2.10 / Skill 1.0.1 / 事件协议 1.0 / 管理契约 1。

四项 GitHub 资产均已上传，完成匿名完整下载、文件大小和 SHA-256 校验。release.json 记录实际地址、验证时间、GitHub 资产 ID 与运行源码提交；SHA256SUMS 和 SeeEnd-build.json 保留原始构建数据。清单中的本地路径相对于本文件，用于三仓库工作区复核。

使用 App 只需安装包。第三方源码与构建材料为可选下载；开发扩展保留商店公钥，商店待提交 ZIP 去掉开发 key。GitHub 发布不代表 Chrome 商店提交或审核通过。

本次修复跨会话误接管、隐藏完成控件、定向设备路由、长任务生命周期、配置修复的禁用偏好、多目录卸载、OpenCode 归属检查和自检端口；新增 App/Skill 能力契约、同任务脱敏证据和发布身份校验。

[公开发布页](https://github.com/YipengChen/SeeEnd-downloads/releases/tag/v0.8.6) · [测试说明](TESTING.md) · [构建身份](SeeEnd-build.json) · [许可](LICENSE) · [第三方许可](THIRD_PARTY_NOTICES.md) · [动态库替换](LIBRARY_REPLACEMENT.md)

本目录的许可说明与 App/第三方 ZIP 内说明是同一构建时快照，其中“待发布”描述当时状态；实际发布结果以 release.json 为准。receipt 的旧 Git revision 与 dirty 标记也是构建时事实，提交后没有追改签名包。HTTP／IPC 与安装包真实辅助进程验证已补齐；真实宿主、新网站任务及实体设备边界见测试说明。
