# SeeEnd 架构与目录

以 App 0.8.67、扩展 0.2.14 的当前源码为基线。用户操作见[使用指南](quick-start.md)，已实现范围见[产品基线](product/CURRENT_PRODUCT.md)，构建命令见 [README](../README.md#开发测试与构建)。

## 模块边界

```text
app.py                         桌面与打包辅助进程入口
aiflame/
  core/                        事件校验、任务生命周期、历史与用户数据路径
  hub/                         鉴权 HTTP 接口、任务编排、服务生命周期和重置
  outputs/                     灯光、系统通知与微信通知的输出调度
  desktop/                     原生 Qt 窗口、菜单栏／托盘、桌面火焰与设备操作
  devices/                     六款型号、配置、发现、凭据与点阵灯效
  transports/                  Yeelight、像素帧、miIO 及多设备通信
  integrations/
    codex/                     Codex Hooks、诊断与按需终端定位
    cli_tools/                 其他编程工具 Hooks、插件和轮次关联
    chatgpt_web/                九平台浏览器扩展与 Native Messaging
    local_api/                 鉴权 HTTP 客户端
    manual/                    手动模拟事件
  assets/                      图标与翻译资源
packaging/                     原生编译、冻结验证、许可材料与分发归档
tests/                         自动回归、隔离截图与手动实机验收工具
docs/                          当前指南、架构、发布材料及历史研究
```

桌面使用 PySide6 Qt Widgets，不加载 Qt WebEngine。App 可在进程内运行 Hub，`--hub` 提供独立无界面服务。单实例协调恢复已有窗口；隐藏窗口停止完整页面轮询，托盘单独刷新轻量状态，Hub 和提醒继续运行。退出只停止本 App 启动的 Hub。

macOS 的 `desktop/macos_accessibility.py` 在创建 Cocoa 平台后安装进程内选中项保护，通过有效接口和当前模型读取选择，过期节点返回空选择，避开 Qt 原生失效指针；不修改系统权限或 Qt 动态库，不关闭无障碍服务。

## 事件与输出

`Engine` 校验协议 1.0 事件、来源策略、序号和生命周期。状态为 `idle`、`working`、`needs_input`、`done`、`cancelled`、`timeout`、`unknown`；旧 `error` 转为 `unknown`，不再作为单独提醒状态。`needs_input` 可恢复工作，取消与超时不推断成功。Hub 独立推进租期，续期不重复弹通知。

`OutputRouter` 将准入事件交给各输出，分别记录投递结果；重复事件不重复投递，单个通道失败不阻断其他通道。`desktop_notifications`、`wechat_notifications`、`desktop_flame_enabled`、`hardware_notifications` 独立保存，首次启动／重置只开启桌面火焰；旧 `mode` 仅用于迁移设备提醒开关。

`FlameOutput` 管理状态、提醒时长与演示，设备传输按型号渲染。`DesktopOutput` 保存待通知事件，桌面以鉴权 claim／ack 接口一次性领取并回报；过期或失联不补播。微信独立处理绑定、连接与发送。来源开关作用于各输出，亮度和实体设备连接不影响系统／微信通知。接受请求不证明用户已看到提醒。

设备投递上下文绑定发送时的事件、连接与外观。返回的旧 ACK 或心跳不能确认后来的任务和参数。系统通知与音效在领取后、实际提交前再次验证 claim、输出代际、来源、任务状态与有效期；桌面还核对用户刚切换的开关，防止保存请求尚在途中时继续投递。微信就绪状态只使用 90 秒内的成功轮询证据。

快照 `active_count` 统计工作中任务，`unfinished_count` 统计已开启来源的全部工作中／等待输入任务，不受最近任务列表限制。演示与单台试亮共用未结束任务限制，服务端再次校验；新任务取消演示。设备提醒关闭或无设备在线时仅做屏幕演示，有在线设备且开关开启时做设备同步演示；状态卡试亮只作用于当前设备。

## 设备与凭据

`devices/models.py` 是型号与能力的唯一注册表：YLTD003、YLDP005、Beam、Cube Lite、米家插座 3 和 4。`devices/light_settings.py` 定义按状态保存的颜色、亮度、白灯和插座规则；`cube_effects.py` 提供像素状态牌、彗星巡航、贪吃蛇、文字状态四种点阵效果。具体默认值和操作只在[使用指南](quick-start.md#连接设备)维护。

发现按设备身份、所选型号及能力过滤；局域网搜索在约 4 秒接收窗口发出 3 轮查询并按物理 ID 合并，定向 IP 验证成功后提前结束。`devices/service.py` 在后台处理发现、连接和设备操作。SeeEnd 不枚举旧灯板串口或刷写固件。

`FleetTransportManager` 为每台设备维护独立连接与输出状态。屏幕挂灯／W3 使用有限原生 color flow，Beam／Cube 使用像素帧，插座使用局域网 miIO／MIoT。Beam 与 Cube 待机发送黑帧；断网／崩溃后 Beam 可能恢复原厂效果，Cube 可能保留最后画面。协议确认或帧发送不证明实际发光。配网和固件维护由原厂工具完成。

配置按物理 `device_id` 保存。旧 Yeelight 配置继续迁移，AI Flame 灯板记录忽略。关闭设备提醒停止调度并在后台释放连接，重新开启等待旧连接释放后恢复；重连不补播旧完成提醒。

小米扫码云会话与候选凭据短时驻留内存，仅持久化选中设备 Token。macOS 使用私有目录中的 AES-GCM 密文与独立密钥文件，Windows 使用当前用户 DPAPI；日常局域网控制无需持续云登录。微信凭据在 macOS 使用钥匙串，Windows 使用 DPAPI，Linux 使用权限受限文件。重置与删除按所属范围清理凭据，失败保留重试信息。

## 接入与任务返回

`integrations/registry.py` 组合来源策略：六种编程工具、九种网页和本地 API／手动／模拟来源。编程工具安装器只增量维护 SeeEnd 自有 Hook 或插件，Skill 负责调用安装、诊断与验证接口；移动 App 后须更新绝对路径并重载宿主配置。没有桌面客户端监测来源。

网页通过明确 DOM 状态控件识别开始与完成，不读取提问或回复正文。原生桥接按精确 HTTPS 来源选择独立凭据；后台任务通过脉冲检测与续期，系统睡眠、冻结和丢弃不在保证范围。

事件可选 `return_target`／`return_context`；旧事件仍可提交。`core/task_origins.py` 校验有界元数据，仅在开始事件保存不可变位置。网页在 Hub 只保存客户端／定位 ID，URL 留在扩展；终端保存进程身份、TTY、运行实例、会话和可选项目名，iTerm2 另有 GUID。Hook 的 `cli-origin-cache.sqlite` 稳定实例命名空间，重置时清除。

`hub/task_returns.py` 只接受桌面凭据发起用户返回请求（`POST /api/task-return`），可查询异步结果；来源凭据不能主动切换窗口。网页独立控制连接绑定原浏览器配置，重连撤销旧连接。扩展核对会话和浏览器 epoch，再选中原标签页或用已保存的正式会话地址恢复。

Terminal／iTerm2 选择使用固定 AppleScript 和有界参数，不注入命令或终端输入。后台线程先请求自动化授权，再次校验进程／会话后选中标签页或分屏。普通 Codex 共享 daemon 缺少客户端 TTY 时，按需只读查询原生会话名称和完整目录，以存活 CLI、标题及目录判断当前活位置唯一性；不统计历史重名，也不保证历史任务仍是原客户端。歧义或读取不完整拒绝返回，不查询对话正文、终端内容或后台持续扫描。

## 数据与发布边界

| 系统 | 默认数据目录 |
| --- | --- |
| macOS | `~/Library/Application Support/AI Flame` |
| Windows | `%LOCALAPPDATA%/AI Flame` |
| Linux | `$XDG_DATA_HOME/ai-flame`，默认 `~/.local/share/ai-flame` |

保留 `aiflame` 包名、应用／桥接标识与旧 CLI 别名以兼容已有安装。`AIFLAME_DATA_DIR` 可隔离测试。事件历史最长 7 天、最多 10,000 条，去重保留 24 小时；运行中任务保留至结束或租期失效。网页返回缓存最长 7 天、最多 1,000 项。数据目录含凭据，不随分发包或测试夹具公开。

桌面与 Skill 的管理客户端先校验短时挑战的 HMAC 服务端证明，再对方法、完整路径、请求体和唯一 nonce 签名。请求与响应绑定 Hub 实例和配置代际；管理接口不接受明文 dashboard Bearer 或 Cookie，来源 Bearer 仍仅用于对应来源。重置同步撤销旧代际，旧请求不能取得新管理凭据。管理根凭据只在本机私有存储和使用它的进程内读取，不通过 HTTP 发送。

发布顺序为 `build.py` → `verify_frozen.py` → `archive.py`；构建身份包括 App／扩展版本、源码指纹及第三方材料，禁止用旧 App 改名充当新构建。各操作系统需原生构建与分别验收。第三方许可证随包，源码材料单独提供；当前签名、商店与公开分发状态见[发布说明](REPOSITORIES.md)。历史结果不替代当前平台与设备的[实际验收](validation.md)。

跨仓检查还核对当前 prepared 清单的源码指纹、外部构建收据，以及安装包内外的收据；已发布历史记录按当时事实保留。
