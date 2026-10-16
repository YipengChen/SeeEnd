# 仓库协作与发布

| 仓库 | 维护内容 | 对齐依据 |
| --- | --- | --- |
| code/（SeeEnd） | App、Hub、适配器、扩展、测试、产品文档与构建 | pyproject / __version__、integration-contract、构建 receipt |
| skill/（SeeEnd-skill） | Skill、参考资料、脱敏客户端与离线测试 | 实际 App 契约及同任务验证证据 |
| downloads/（SeeEnd-downloads） | 下载入口、公开政策和版本发布清单 | release-status 中 published / prepared 及资产 SHA-256 |

当前基线为 App 0.8.56、扩展 0.2.13、Skill 1.0.3、事件协议 1.0、管理契约 1。Skill 的入口、参考资料和测试快照均对齐 App 0.8.56；Skill 与扩展保持独立版本号。公开下载以 `downloads/release-status.json` 的 published 记录及已核验资产为准。商店状态见[日期化操作记录](chrome-web-store/APPLICATION_STATUS.md)。

## 内容维护

[文档索引](README.md) 指向各主题的唯一维护入口。接入操作由独立 Skill 的 references 维护；App 返回的 `/api/integration-contract` 代表实际安装版本的字段、来源与能力。`/api/verification-evidence` 提供本次窗口同一任务的脱敏证据；一个来源成功不能证明另一目录、Profile 或会话也已接入。

旧 `aiflame` 名称、数据目录、CLI 和开发扩展 ID 仅为兼容保留。历史研究、旧验收与提交材料保留其日期范围，不复制为当前功能说明。

## 构建与发布

1. 运行相关 Python、JavaScript 和 Skill 回归，在工作区执行 `code/.venv/bin/python -B tools/check_workspace.py --code code --skill skill --downloads downloads` 检查契约、政策和发布材料。
2. 固定源码后，用项目虚拟环境依次运行 `packaging/build.py`、`packaging/verify_frozen.py`、`packaging/archive.py`，统一使用同一个 `--dist-dir`。构建期间不修改被指纹覆盖的文件。
3. 运行 `packaging/extension_release.py` 生成同版本开发包及商店格式 ZIP。保留浏览器已加载目录及历史提交包。
4. 将本地新版本登记为 `prepared`，清单记录版本、构建 receipt、资产路径及摘要。App、第三方材料、扩展与商店包分别校验；源码指纹和随包 receipt 必须匹配。
5. 只有实际发布并验证公开下载后才更新 `published` 和主下载入口。上传、商店提交、审核通过和网页部署分别记录。

公开资料不得包含用户数据、凭据或 SeeEnd 原创源码；第三方材料按已校验 manifest 选取。当前 App 和构建 receipt 位于 `dist/SeeEnd.app`、`dist/SeeEnd-build.json`；版本化 ZIP、扩展包和分发 receipt 位于 `dist/gemini-0.8.56/`。旧 0.8.55 App 和本地包已按用户要求清理，历史版本清单保留原摘要用于追溯。工作区验证日志位于 `validation/`，不加入源码或公开下载仓库。

2026-10-17 完成 Windows x64 云端构建，App ZIP、匹配的第三方源码包、receipt 与 `SHA256SUMS` 已下载到 `dist/windows-0.8.56/`。构建来源为提交 `2598da203d7af04b9b453133428e352e6cbfb11f`，验证记录见[Windows 构建验收](validation.md#2026-10-17windows-x64-云端构建)。该包目前保存在本机与私有 Actions artifact；现有公开 macOS Release 资产保持原身份。Windows 第三方源码 ZIP 与 macOS 同名但内容及摘要不同，公开分发时须使用平台后缀，避免覆盖旧资产。
