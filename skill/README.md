# SeeEnd Skill

让熟悉的编程工具为自己安装、检查和维护 SeeEnd 原生 Hook 或插件。安装完成后由原生事件持续提醒，不要求模型每次记得上报。

Skill 入口保持简短，产品、接入、协议、排查和更新资料按需读取。本目录包含可独立安装的接入技能、辅助客户端和测试；与 App 在同一仓库维护，不包含用户配置。

## 安装与使用

取得仓库：

```sh
git clone https://github.com/YipengChen/SeeEnd.git
```

将仓库中的完整 `skill/seeend/` 目录安装到编程工具支持的 Skill 目录，保留 `references/`、`scripts/` 和 `agents/`。具体加载位置、重载方式和调用语法以宿主官方文档为准；支持 Skill 文件格式并不保证能执行本机 Hook 或访问本机服务。

原创 Skill、文档和脚本采用 [GPL-3.0-only](LICENSE)；安装目录包含同一许可，保留此前已经授予的权利。

安装后可提出：

- “使用 SeeEnd Skill，为当前编程工具安装 SeeEnd 接入。”
- “使用 SeeEnd Skill，只检查现有接入，不修改配置。”
- “使用 SeeEnd Skill，排查为什么有开始提醒但没有完成提醒，并修复。”
- “使用 SeeEnd Skill，解释桌面火焰、系统通知和设备提醒的关系。”
- “使用 SeeEnd Skill，检查升级后是否需要迁移接入。”

单独调用且没有目标时默认只读检查。用户明确请求安装或修复后，执行目标范围内可撤销的操作；Hook 信任和系统权限仍需在宿主真实界面完成。

## 前提与兼容范围

- SeeEnd 本体在用户电脑安装并运行；没有灯具也能使用默认开启的桌面火焰。Skill 不提供 APP 下载包，也不需要模型 API Key。
- 编程工具提供原生扩展能力，执行环境能访问 SeeEnd 本机回环服务。SSH、容器或云端的 localhost 不代表用户电脑。
- Skill 1.0.6 基线为 SeeEnd 0.8.69、管理契约 1、事件协议 1.0，执行时核对实际能力。内置接入覆盖 Codex、Claude Code、Qoder CLI、DeepSeek Harness、Gemini CLI、OpenCode；具体入口和模式需真实验收。实际连接必须支持 HMAC 双向鉴权；不支持的旧 App 需先由用户选择升级，客户端不匿名降级。
- 未预设工具可尝试 `local-script` 实验适配，共用来源身份、开关及验证范围；当前版没有动态注册独立工具来源的接口。
- 可选辅助客户端使用 Python 3 标准库，仅在本机读取当前用户数据目录中 `credentials.json` 的 `dashboard` 作为 HMAC 根密钥，不输出或发送根秘密，不使用管理 Bearer 或 Cookie。先验证随机挑战中的服务实例、代际及应用身份，再逐请求签名并验证响应；禁止重定向和重放。支持 `AIFLAME_DATA_DIR`，不会创建或修改凭据文件，也不会匿名回退。默认只读，修改操作需要明确 `--apply`；该开关不能代替用户授权。无 Python 时可按 API 参考用宿主已有 HTTP 能力。

## 目录

```text
seeend/
  SKILL.md                 精简入口与任务路由
  agents/openai.yaml       Codex UI 元数据
  LICENSE                  安装包许可与版权声明
  references/              按需读取的产品与操作资料
  scripts/seeend_client.py 本机会话与接入客户端
tests/                     独立客户端离线测试
```

辅助客户端示例，从 `seeend/` 目录执行：

```sh
python3 scripts/seeend_client.py status
python3 scripts/seeend_client.py contract
python3 scripts/seeend_client.py check --source claude-code
python3 scripts/seeend_client.py evidence --source claude-code
```

开发验证：

```sh
python3 -m unittest discover -s skill/tests -v
```

这些离线检查不代替实际宿主的 Hook 加载、信任和新任务验收。不要手工向正式来源发模拟开始与完成来认定接入成功。

CI 在同一仓库检查 App 声明式契约和 Skill 客户端。也可显式选择另一个 App checkout（Python 至少 3.11）：

```sh
SEEEND_APP_ROOT=/absolute/path/to/app-repository PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest discover -s skill/tests -v
```

`tests/fixtures/integration-contract.json` 仅是测试快照，不作为运行时能力来源。契约测试仅导入 App 的声明式 registry，不启动服务、不加载用户配置、不发送任务事件。
