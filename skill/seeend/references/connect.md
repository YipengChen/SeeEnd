# 原生安装与检查

基线：SeeEnd 0.8.69。优先读取实际 App 的 guide/check；它们生成与本机安装路径匹配的辅助命令，不需要访问 SeeEnd 私有源码。

## 识别与安装

确认实际宿主产品、入口、版本、运行主机、SeeEnd 位置与版本、配置目录或 Profile。用户 Shell 与 App 的环境可能不同。默认仅接入所选工具的实际用户级配置；明确要求项目级时核对支持及诊断范围，避免重复两层。

SeeEnd 未运行时打开已经安装的 App，确认托盘中是否已有实例，不另开重复 Hub。macOS 应先将 App 放在稳定位置。未安装时用用户提供或能核实来自发布者的发行包；缺少可靠安装包就只询问包或地址，不猜公开下载站或 `pip install seeend`。编程工具自身的登录与模型配置不属于 SeeEnd 接入。

1. 读取当前 App 的 contract 和所选工具的 plan/check，核对支持范围、目录、Profile、文件归属和版本。DeepSeek 不支持 config-dir；用 check 返回的已有 Profile 列表确认用户实际选择。调用方法见 [api.md](api.md)。
2. 请求接入或修复时，用当前内置 install 增量操作；只检查时不执行。手动合并仅采用真实返回片段，先备份原始字节，保留所有无关字段与 Hook 组，不创建重复注册。
3. 安装后再 check，插件必须包含入口、生命周期模块、桥接和归属等完整组件。文件冲突、符号链接或未知格式先查归属，不用完整模板覆盖。
4. 按实际宿主加载与信任规则处理。不能在承载当前对话的工具里直接杀掉自己；交代重开和审阅步骤，完成其他可执行工作。
5. 重载后验收真实新任务；旧安装对话可能缺少开始信号，不能作为验收。

## 工具差异

| 来源 | 参考配置及加载重点 |
| --- | --- |
| `codex` | `CODEX_HOME/hooks.json`，默认 `~/.codex/hooks.json`；App 生成 `--codex-hook` 命令。核对实际支持 Hooks 的入口，在 `/hooks` 审阅当前定义；新增或变更可能需重新信任。不要为接入修改 config.toml 或信任记录。 |
| `claude-code` | `CLAUDE_CONFIG_DIR/settings.json`，默认 `~/.claude/settings.json`；核对 `/hooks`、`disableAllHooks` 和管理策略，必要时重开。 |
| `qoder` | 默认 `~/.qoder/settings.json`；验证 Qoder CLI，不能据此验证 Qoder 编辑器或 QoderWork。 |
| `gemini-cli` | 默认 `~/.gemini/settings.json`；重开并按版本核对 `/hooks panel`、hooksConfig 和禁用列表。超时单位以 App 返回片段为准。 |
| `opencode` | OPENCODE_CONFIG_DIR 或 XDG_CONFIG_HOME/opencode，默认 `~/.config/opencode`；专用插件，不改 opencode.json/jsonc。重启实际加载插件的服务或进程，刷新客户端可能无效。 |
| `deepseek-harness` | DSH_HOME/profiles 或默认 `~/.dsh/profiles` 下的已有 Profile。App 所在环境须能找到 dsh/pnpm，使用同一 Profile 启动；依赖及锁文件由管理器维护。 |

除 Codex 外，内置 CLI 辅助入口为 `--cli-hook <内置来源>`。完整冻结程序路径或解释器由 App 生成，不自行拼写或假定系统存在运行时。助手退出码零可能代表静默忽略或网络错误被屏蔽，不证明事件已收到。

需要查语义或版本差异时只查相应官方资料：[Codex](https://learn.chatgpt.com/docs/hooks)、[Claude Code](https://code.claude.com/docs/en/hooks)、[Gemini CLI](https://geminicli.com/docs/hooks/)、[OpenCode](https://opencode.ai/docs/plugins/)、[Qoder CLI](https://docs.qoder.com/cli/hooks)、[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/basic/publish.md)。地址失效时从厂商官方入口定位；文档须匹配实际安装版。

## 真实验收

只读检查读取已有窗口及历史，不创建新窗口，注明证据时间和范围。安装、修复或更新后的验收：

1. 确认来源开启、Hook/插件加载及必要信任，再开始新验证窗口；记录 `verification[source].since`，之后不反复重置。
2. 让用户在所选目录/Profile 对应的会话发送新任务，可以用：“请只回复：SeeEnd 接入测试成功。不要读取或修改文件，也不要调用工具。”这使用宿主账号的普通请求额度，可用原本的新任务代替。
3. 用只读 evidence 查询当前窗口事件，对比同一 `task_id` 的 `working/seq=1` 与更大序号的 `done`，确认 `simulated=false` 且 `status!=unmatched`，并将 task_id、事件时间与目标宿主会话及实际结束时机关联。客户端排除模拟、被关闭来源忽略及窗口前事件。
4. 不调用上报接口或 Hook 助手手工造出正式来源的开始/完成。模拟只能用登记测试或隔离桩，结果单独报告。

验证按来源聚合，结合实际会话、任务 ID 与时间确认归属；无法排除其他目录、Profile 或脚本任务时标为证据不足。简单回复通过只证明该场景；按用户要求再验证长任务、等待恢复、取消、整体失败、并发与后台。

evidence 的 `truncated=true` 表示窗口事件超过返回上限；事件保留也受 7 天/10000 条上限影响。缺少所选任务的开始或终态时标为证据不足，不用 summary 填补证据。

App 0.8.4/0.8.5 是历史资料基线，缺少 evidence 接口；仅凭其来源级 verified 不完成当前配置验收。当前客户端要求 HMAC 双向身份校验；保留旧响应格式解析不表示支持连接这些旧 App，也不代表建议自动升级。

区分配置就绪、实际加载、接收事件、真实验收与提醒可见。软件投递或灯具回执不能证明用户看到了横幅或物理灯光。
