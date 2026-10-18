# 事件协议与生命周期

基线：事件协议 1.0、SeeEnd 0.8.69。仅自建适配或实际协议变更时读；已有内置适配优先使用 App 提供的助手。

`POST http://127.0.0.1:8765/v1/events`，JSON，`Authorization: Bearer <对应来源令牌>`。由本地代码按需读取合法令牌，禁止输出。请求体上限 4096 字节，仅传生命周期元数据。

| 字段 | 要求 |
| --- | --- |
| schema_version | 字符串 `1.0`。 |
| event_id | UUID；一个逻辑事件一个 ID，重试复用 ID 与序号。 |
| source_id | 与已注册来源及令牌完全匹配。 |
| task_id | 1–120 字符；同一轮不变，新一轮换 ID，不能包含正文。 |
| seq | 整数 0–2147483647，不能是 bool；开始推荐 1，随后严格递增。 |
| state | idle、working、needs_input、done、cancelled、timeout、unknown。 |
| occurred_at | 动态 UTC 时间，带 Z 或 +00:00。 |
| ttl_ms | 整数 1000–300000，推荐 60000，表示新鲜度，不是任务最大时长。 |
| reason | 可选：completed、failed、cancelled、timeout 或 null；没有原因推荐省略。 |
| device_id | 可选；普通编程工具接入省略，由 App 管理输出。 |

八个基础字段必填。普通自建适配仅按需增加 reason/device_id；当前契约另声明内置返回任务使用的 return_target/return_context，不自行构造。不能加 metadata、正文、任务标题、工具版本或 simulated。维护信息放在本地安装记录。

当前版已移除 error 状态；失败使用 unknown/failed。旧 App 的 error 历史证据仍可读取，不向当前 App 发送该状态。

| 原生信号 | 保守映射 |
| --- | --- |
| 新一轮开始 | working，seq=1。 |
| 明确活动/恢复 | working，递增序号。 |
| 等待授权/输入 | needs_input；等待时不要用工作续期掩盖。 |
| 正常根轮次结束 | done，reason=completed。 |
| 整轮失败 | unknown，reason=failed；可恢复的单个工具失败不代表整轮失败，不触发提醒。 |
| 取消/中断 | 推荐 idle，reason=cancelled；此 reason 只与 idle 配合。 |
| 明确超时 | timeout 或 unknown，reason=timeout；不得与 done 配合。 |
| 结果不确定 | unknown；不猜成功。 |

## 必须处理的约束

- 内置编程工具来源要求先登记 working/seq=1，未登记后续可能返回 200 但被忽略。
- event_id 或同一来源/任务/序号用于去重。旧序号或非法覆盖终态会被拒绝；普通自建来源不假定支持部分内置来源的迟到取消例外。
- 新轮次换 task_id；多会话区分任务；同一任务串行发送，序号用锁或事务保护，不读取会话正文进行关联。
- 超过 ttl 或未来偏差超过 30 秒会被拒绝。不能改时间补播离线旧完成；检查本机时钟。
- local-script 当前工作租期 120 秒；长任务需基于可靠宿主信号续期。内置原生来源主要依赖明确终态，租期策略不同。
- 定时续期必须仍能确认宿主运行；已结束、等待、失联或不确定时不持续声称 working。队列、网络超时、输入大小、日志和重试均有界。
- 不把单次工具完成、子代理完成、临时 idle 或可能继续执行的 Stop 当成最终根任务完成，依据宿主实际语义判断。
- 通知失败不得改变宿主权限或控制流，不阻塞模型，不把 Hub 失联上报为编程任务失败；Hook 输出满足宿主的中性格式。
- 202 表示事件处理，200 可能是重复或忽略；查看 duplicate/result/phase/deliveries，不能证明横幅或灯光可见。

禁止手工向正式来源发假任务验证接入，协议合规不证明触发链真实。错误分类见 [api.md](api.md)。
