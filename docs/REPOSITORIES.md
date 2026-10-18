# 单仓库维护与发布

应用、Hub、浏览器扩展、Skill、政策网页与构建流程统一维护于 [YipengChen/SeeEnd](https://github.com/YipengChen/SeeEnd)。仓库保持私有，由维护者在准备完成后手动改为公开；CI 不修改可见性。

| 路径 | 内容 |
| --- | --- |
| `aiflame/` | App、Hub、设备协议和接入适配器；扩展位于 integrations/chatgpt_web/extension |
| `skill/seeend/` | 可独立安装的 Skill，含客户端、参考资料与 GPL 许可 |
| `tests/`、`skill/tests/`、`tools/` | 应用、浏览器、Skill 回归及离线契约检查 |
| `packaging/`、`.github/` | 各平台构建、冻结验证、制品校验和草稿发布 |
| `docs/`、`website/` | 使用与开发文档、唯一政策 HTML 和历史发行清单 |

App、扩展和 Skill 保持独立版本号。应用构建 receipt 的 `source_sha256` 是应用与构建文件的指纹，不声称覆盖全部文档和 Skill；完整仓库身份由 Git 提交和同提交源码 ZIP 证明。旧版本清单保留其下载 URL、许可及原始摘要，源码版本不要求等于历史已发布版本。

## 本地开发

从仓库根执行 README 中的安装与测试命令。额外检查：

```sh
python -m unittest discover -s skill/tests -v
python -m unittest discover -s tools -p 'test_*.py' -v
python tools/check_workspace.py
```

`check_workspace.py` 默认验证源码版本、许可一致性及真实 App/Skill 契约，不启动服务或读取用户配置。`--release` 是旧发行清单的显式审计模式；它不替代新 CI 制品检查。

`dist/`、第三方源码缓存、生成许可、虚拟环境和 node_modules 不提交。个人配置、真实验收证据、模型视频与维护备份保存在仓库之外。禁止把本地构建产物推入 Git。

## GitHub 构建与发布

1. 推送源码或 PR 后，Tests 检查 Python 3.11/3.13、JavaScript、Skill 和单仓契约。
2. 在 Actions 的 `Build desktop and draft release` 手动选择 all，可构建 macOS arm64、macOS Intel、Windows x64、Linux x64。构建使用 GitHub runner，含测试、Cython 编译、冻结 Helper 验证和归档；手动运行只生成 artifact。
3. 正式准备版本时，提交版本号并推送匹配的 `v<app_version>` tag。CI 检查 tag 与源码版本一致，全部平台、扩展、完整源码和 Skill 构建成功后，自动创建同仓库的 **草稿 Release**。
4. 草稿包含各平台 App、对应第三方源码、receipt、浏览器开发包与商店包、完整 SeeEnd 源码 ZIP、独立 Skill ZIP，以及统一 SHA256SUMS。聚合验证拒绝缺失、重名、篡改、版本或提交不匹配的文件。
5. 维护者完成目标系统与真实提醒验收后，可在 GitHub 手动发布草稿。仓库私有期间，Release 也需要仓库权限。公开仓库和发布 Release 是两个独立动作。macOS 构建使用 ad-hoc 签名，不代表 Apple 公证；Windows 也不自动取得发行签名。

本地构建用于调试与验收，仍按 build → verify_frozen → archive 执行。常规对外版本由 Actions 构建，不从本地上传大型安装包。

## 旧仓库与政策域名

`SeeEnd-skill` 和 `SeeEnd-downloads` 保留旧发行记录与已有安装链接，后续源码统一在本仓库维护。合并不追改历史资产摘要。旧的政策网址仍供已上架扩展使用；`website/` 是未来部署源，在更换托管与商店 URL 前保持旧网址可用。私有仓库阶段不自动部署政策站。

GPL 许可与第三方边界见 [LICENSE](../LICENSE)、[第三方声明](../THIRD_PARTY_NOTICES.md)及[许可说明](legal/RELEASE_LICENSING.md)。
