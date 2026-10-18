# 本机管理接口

基线：SeeEnd 0.8.69、管理契约 1、事件协议 1.0。这些是已有实现接口，不是永久兼容承诺。默认地址 `http://127.0.0.1:8765`，先核对监听进程与 `/api/session` 返回的应用身份和版本，再读取 `/api/integration-contract`。不得编造动态来源注册或自动更新命令。

## 客户端使用

优先运行本 Skill 的 Python 3 标准库脚本。相对路径以 Skill 根目录为基准；有些宿主执行目录不同，使用脚本实际绝对路径。

```sh
python3 scripts/seeend_client.py status
python3 scripts/seeend_client.py contract
python3 scripts/seeend_client.py plan --source claude-code
python3 scripts/seeend_client.py check --source claude-code
python3 scripts/seeend_client.py evidence --source claude-code
```

`install`、`uninstall`、`verify` 会修改配置或验证窗口，必须在用户请求范围内，并显式传 `--apply`。这只减少脚本误调用，不增加一次用户确认流程。需要具体用法时查看脚本 `--help`。

没有 Python 时用宿主已有的本机 HTTP 能力实现下述接口，不为只读检查擅自安装运行时。脚本仅在本地读取当前用户 `credentials.json` 中的 `dashboard` 建立会话，不输出凭据，不上报事件，也不执行真实 AI 请求。

客户端按实际契约的 `sources` 和 `capabilities.native_sources` 选择支持来源，不从 App 版本号推断能力。客户端识别已知的旧状态集合与当前已移除 error 的集合，并核对已知可选字段；旧 App 的 error 证据只作为历史状态读取。契约版本、事件协议或字段不兼容时停止操作。旧响应格式的有限只读解析兼容仅适用于已通过 HMAC 双向身份校验的服务。不支持 HMAC 的旧 App 无法通过本客户端连接，不能只读降级、写配置或进行同任务证据验收；是否升级由用户决定。

DeepSeek Harness 使用实际已有 `--profile`，配置根目录来自 SeeEnd 进程的 `DSH_HOME`；客户端和 App 均拒绝对它使用 `--config-dir`。`check` 保留已有、登记及已安装 Profile 列表；不要据此猜测当前正在使用哪个 Profile。

## 身份与请求

管理请求使用 `seeend-hmac-v1` 双向鉴权。`dashboard` 仅作为本地 HMAC 根密钥，绝不作为 Bearer、Cookie 或其他字段在线路发送。辅助客户端先向 `/api/auth/challenge?nonce=<随机32位hex>` 领取挑战，验证覆盖随机 nonce、服务实例、配置代际、有效期及应用名称/版本的 HMAC；每个请求再签名 method、完整 path/query、body SHA-256、实例/代际、随机 nonce 和有效期，并验证绑定该请求的响应状态与正文签名。服务端拒绝过期、重放及跨实例/代际请求；nonce 缓存有界。重置后重新读取本地密钥并验证新挑战，在途旧请求不能获得新凭据。普通来源 Bearer 仅能访问来源接口；管理接口不接受旧 Bearer、Cookie 或匿名回退。所有请求绕过代理并禁止任何重定向。

凭据文件位置与 App 一致：优先使用 `AIFLAME_DATA_DIR/credentials.json`；默认 macOS 为 `~/Library/Application Support/AI Flame/credentials.json`，Windows 为 `%LOCALAPPDATA%/AI Flame/credentials.json`，Linux 为 `$XDG_DATA_HOME/ai-flame/credentials.json`（未设置或为相对路径时使用 `~/.local/share/ai-flame/credentials.json`）。文件缺失、无权限或格式错误时只返回脱敏错误，停止请求；不创建配置、不调整权限、不匿名回退。

| 方法及路径 | 参数与作用 |
| --- | --- |
| `GET /api/auth/challenge?nonce=...` | 公开的短期 HMAC 服务证明；根密钥不发送；验签失败就停止。 |
| `GET /api/session` | 逐请求 HMAC 签名；返回 `app_name`、`app_version`，不设置 Cookie；没有识别为 SeeEnd 就停止。 |
| `GET /api/integration-contract` | 管理会话；返回管理契约版本、App 身份与版本、事件字段/状态/原因、来源策略及 capabilities；不含凭据。 |
| `GET /api/status` | 管理会话；来源、设置、验证、近期事件、投递信息。只提取当前任务需要的字段。 |
| `GET /api/integration-guide` | Query：`source`、可选 `config_dir`、`profile`；为实际契约支持的内置工具生成 files、changes、snippet、加载步骤。DeepSeek 不接受 config_dir。路径须 URL 编码。 |
| `GET /api/integration-check` | 同样 Query；检查所选配置和接收证据。按所选目录与 Profile 查询。 |
| `POST /api/integrations/install` | JSON：`source`、可选字符串 `config_dir`、`profile`。增量安装；首次注册启用来源，修复已有注册保留来源开关。仅在来源开启时开始新验证窗口；成功仅说明配置步骤完成。 |
| `POST /api/integrations/uninstall` | 同安装参数；仅移除明确要求的内置 SeeEnd 接入。 |
| `POST /api/verification` | JSON：`{"source":"实际来源"}`；目标来源须开启。开始新窗口，只读检查不调用。 |
| `GET /api/verification-evidence` | Query：`source`；返回当前窗口 since、来源级 summary 及事件 event_id、task_id、seq、state、reason、created、simulated、status，仅含生命周期元数据。保留 total/limit/truncated 与 retention；最近最多 1000 条，截断时不能认定缺失开始或终态的任务已验收。只读，不重置窗口。 |
| `POST /api/settings` | 只提交需要的设置。修改 `source_enabled` 时读取最新完整映射，只变更目标键，提交完整映射，避免并发覆盖并核对结果。 |
| `POST /api/trigger-test` | JSON：`{"source":"支持测试的来源"}`，登记模拟测试；`GET /api/trigger-test` 读取结果。按需使用，不能代替验收。 |
| `GET /v1/status` | 来源 Bearer；返回该来源及输出状态，具体字段以实际版本为准。不是完整诊断或协议发现。 |
| `POST /v1/events` | 来源 Bearer；标准事件，见 [protocol.md](protocol.md)。 |
| `GET /v1/events/<event_id>` | 对应来源或管理会话读取处理结果，不能凭一个事件认定接入成功。 |

内置其他 CLI 汇总用 `/api/cli-integrations`，Codex 用 `/api/codex`；精确检查优先 `/api/integration-check`。当前来源按工具汇总，不能由配置检查得知宿主实际加载目录或信任状态。用 evidence 查询同任务的开始与结束，再核对目标会话；事件元数据没有正文。

## 超时与错误

安装可能调用外部插件管理器，使用有界较长超时，例如 60 秒。超时后先 check，服务端可能已经完成或仍在操作；不能并发重复安装。失败结果不得当成成功。

| 状态码 | 检查方向 |
| --- | --- |
| 400 | 字段、格式、参数或来源不支持。 |
| 401 | 会话或来源鉴权；建立正确会话，不转储凭据。 |
| 403 | 来源对应关系、管理身份、Host、Origin 或跨来源请求。 |
| 404 | 路径或事件不存在；核对实际版本，不能直接推断需要升级。 |
| 409 | 操作、配置或生命周期冲突；客户端只使用允许的结构化 error_code 分类，不输出原始正文。 |
| 422 | 事件过期或时钟偏差。 |
| 429 | 请求频率过高，进行有界等待，不循环高速重试。 |

对未知版本的完整文本、snippet、日志和新字段先审查再输出或执行。键名脱敏无法保证文本值没有秘密。

接入 409 的安全分类包括 integration_conflict、config_invalid、missing_dependency、io_error、plugin_workspace、plugin_network、plugin_timeout。未知分类使用一般失败说明；安装超时或 IO 错误仍可能部分完成，不能凭错误分类立即重复写入。
