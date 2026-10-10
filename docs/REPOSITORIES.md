# 仓库协作与发布

| 仓库 | 维护内容 | 对齐依据 |
| --- | --- | --- |
| SeeEnd（code/） | App、Hub、适配、浏览器扩展、构建与产品文档 | pyproject 与 __version__，App API integration-contract，构建 receipt |
| SeeEnd-skill（skill/） | Skill 入口、按需参考、脱敏客户端与离线测试 | App 提供的来源和 capability；契约版本与同任务证据 |
| SeeEnd-downloads（downloads/） | 隐私政策、公开下载、使用说明与发布清单 | 已发布资产与待发布资产分别记录；匹配 App/扩展版本、文件名与 SHA-256 |

当前准备组合是 App 0.8.6、扩展 0.2.10、Skill 1.0.1、事件协议 1.0、接口契约 1。对 App 0.8.4/0.8.5 的兼容回退仅作只读检查；完整接入与任务证据需要提供新契约的 App。旧品牌、数据目录、CLI 和开发扩展 ID 继续用于迁移。

## 内容权威与检查

当前操作统一在本仓库 README、quick-start 与 CURRENT_PRODUCT。接入技能以独立 Skill 的 references 为唯一维护入口；seeend-integration-prompt.md 只保留跳转，不再复制整份接入知识。

App 的 /api/integration-contract 返回来源、协议字段和能力；/api/verification-evidence 返回指定来源、本次窗口的脱敏任务证据。来源级成功不能证明另一目录、Profile 或会话接入成功。跨仓库测试由 Skill 的 App contract 检查校验；独立 Skill CI 保留标准库离线测试。

## 本地构建与发布顺序

1. 在 code/ 运行 Python、JavaScript 回归及跨仓库契约检查。
2. 用项目 .venv 依次运行 packaging/build.py、packaging/verify_frozen.py、packaging/archive.py。构建会准备所需第三方材料并验证核心原生模块。
3. packaging/extension_release.py 生成与源码版本一致的独立扩展和新商店提交包；保留 docs/chrome-web-store/ 的旧 0.2.8 历史证据与旧开发 ID 迁移目录。
4. SeeEnd-build.json 记录版本、源码摘要、Git revision/dirty、平台和构建环境；归档时拒绝不匹配源码的 App。对应发布清单列出安装包、可选材料及 SHA-256。
5. downloads/ 保留当前已发布入口；新版本内容先存入 releases/ 对应版本目录，标明 prepared。资产真正发布并核验后再提升首页下载入口和发布状态。准备文件、提交代码、上传资产、公开可下载四个步骤分别记录。

公开资料白名单不得包含 aiflame/、用户数据、凭据或 SeeEnd 原创源码归档。第三方源码材料按已校验 manifest 选取；sdist 排除本机构建缓存。任何本地构建或文件同步都不自动代表商店重新提交、审核通过或网页已经发布。

## 保留与清理

当前分发文件保留在 dist/，旧包移入 dist/archive/。已加载的旧开发扩展目录保留原位置与 ID，仅标注迁移用途。工作区历史资料/ 保留截图审查、模型过程与备份；唯一样本、正式模型与历史许可不作为重复缓存删除。
