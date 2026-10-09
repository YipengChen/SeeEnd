# SeeEnd 架构与目录

当前支持 Yeelight YLTD003、YLDP005 局域网灯具和虚拟模式。AI Flame 0.1 / 0.2 的 USB、专用 Wi-Fi 协议、固件与调参功能已移除，旧实现保存在 [归档分支](https://github.com/YipengChen/SeeEnd/tree/archive/ai-flame-2026-10-09)。Python 包 `aiflame`、应用标识、旧 CLI 别名、数据目录和浏览器桥接名称保留，保证已有安装能继续使用。

## 目录

以下目录均位于本仓库内：

```text
app.py                         桌面与打包辅助进程入口
aiflame/
  core/                        事件、任务生命周期、状态汇总、历史与数据路径
  outputs/                     输出路由、灯光调度和桌面通知
  hub/                         鉴权 HTTP 接口、服务生命周期、重置
  desktop/                     PySide6 原生窗口、托盘、设置与设备操作
  devices/                     支持型号、配置迁移、发现和连接任务
  transports/                  Yeelight TCP 通信、连接生命周期和多设备管理
  integrations/
    codex/                     Hooks 注册、派发与诊断
    cli_tools/                 其他编程工具 Hooks、插件与轮次关联
    chatgpt_web/                九平台共用浏览器扩展和 Native Messaging
    desktop_clients/           macOS 客户端身份、原生控件识别与诊断
    local_api/                 鉴权 HTTP 客户端
    manual/                    手动模拟事件
  assets/                      图标、Web 控制台与翻译
packaging/                     构建、产物验证、许可证准备与归档
tests/                         自动回归、隔离截图夹具与实机辅助测试
docs/                          使用指南、架构、历史验收及发布资料
```

桌面使用原生 Qt Widgets，不加载 Qt WebEngine。App 可在进程内运行 Hub，`--hub` 提供无界面服务；关闭窗口保留托盘，退出停止本 App 启动的 Hub，独立 Hub 不受影响。单实例协调用于恢复已有窗口。窗口隐藏时停止完整页面轮询，托盘独立刷新轻量状态接口。

## 任务与输出

`Engine` 校验统一事件、来源策略、序号和任务生命周期。`OutputRouter` 将准入事件交给独立输出，每条通路保存投递记录；重复事件不重复投递，单个输出失败不阻断其他输出。

- `FlameOutput` 管理状态颜色、亮度、时长、静默、队列和演示。屏幕烛火用于可视提醒，实体 Yeelight 使用常亮颜色。
- `DesktopOutput` 保存待通知事件；桌面通过鉴权的 claim / ack 接口领取并回报。亮度、灯具连接、灯光暂停和静默不影响桌面通知，来源开关作用于全部输出。
- 通知领取使用一次性 token；失联不重播，过期和服务重启保留明确的未确认状态。系统接受通知请求不能证明用户看到了横幅。
- `needs_input` 可恢复工作；取消与超时保持终态；未知状态不推断成功。续期不产生桌面弹窗，Hub 独立推进租期。

## 灯具与配置

`devices/models.py` 集中定义两款受支持型号；发现响应按型号和原厂 LAN 能力过滤；一次搜索使用同一 UDP socket 在 4 秒内发出 3 轮查询，按物理 ID 合并响应。已知 IP 查询验证到目标后提前结束，重连搜索可在退出时及时取消。`devices/service.py` 在后台处理搜索、连接、试亮、断开和删除。灯具配网和原厂固件升级由 Yeelight App 完成，SeeEnd 不枚举串口或刷写固件。

`FleetTransportManager` 为每台灯具创建独立连接和输出状态，共享 AI 任务及提醒偏好，独立保存状态颜色与亮度。`YeelightTransport` 使用 TCP 55443 和有限时长 color flow，按状态发送常亮颜色；YLTD003 控制 RGB 背光，YLDP005 控制灯泡。协议回执表示指令已被接受，不证明物理发光。

配置按物理 `device_id` 保存。旧配置中的 Yeelight 信息继续迁移，AI Flame 型号记录忽略。虚拟模式立即停止设备调度，在后台关闭旧连接；切回硬件模式等旧连接释放后再恢复。重连不补播历史完成提醒。

## 接入与隐私

CLI 接入增量修改本应用拥有的 Hooks / 插件；移除不覆盖其他配置。移动 App 后重新连接来源，并重启工具会话以更新绝对路径。

浏览器扩展以显式 DOM 控件识别开始与完成，不读取提示词或回复正文；原生桥接按浏览器提供的精确 HTTPS 来源选择独立令牌。隐藏标签的任务通过后台脉冲续期与检测，系统睡眠、页面冻结和丢弃不在保证范围。特殊模式和真实客户端需分别验收。

macOS 桌面实验接入通过 Accessibility 识别控件身份、标签与可用状态，不读取聊天正文或输入值；输入监控只用于保守取消跟踪。权限由用户明确操作触发，未授权、标签变更或跟踪不确定时不猜测完成。Windows / Linux 不加载 macOS 框架。

## 运行数据

源码和安装版均使用系统用户数据目录，兼容名称保留：

| 系统 | 默认目录 |
| --- | --- |
| macOS | `~/Library/Application Support/AI Flame` |
| Windows | `%LOCALAPPDATA%/AI Flame` |
| Linux | `$XDG_DATA_HOME/ai-flame`，默认 `~/.local/share/ai-flame` |

测试可设置 `AIFLAME_DATA_DIR`。数据目录包含凭据，不随源码或安装包分发。历史保留 7 天、最多 10,000 条；去重保留 24 小时，运行中任务保留至结束或租期失效。启动和运行期间定期清理。

## 验证与发布边界

Python / JavaScript 自动测试覆盖模拟协议、生命周期、权限边界和界面行为；截图夹具不连接硬件或读取用户配置。模拟测试不证明真实客户端、系统横幅或灯具发光。

构建、验证和归档依次执行 `packaging/build.py`、`packaging/verify_frozen.py`、`packaging/archive.py`。打包验证包括实际冻结辅助进程生命周期与已删除硬件组件的排除检查。源码分发直接包含仓库内 `docs/`，不再依赖父目录。

当前默认产物为 macOS Apple 芯片 `.app`，使用 ad-hoc 签名；其他系统必须分别构建与实机验收。第三方动态库附许可证、对应源码与替换说明，当前包不含 esptool、mpremote 或 pyserial。原创代码许可见根目录 [LICENSE](../LICENSE)。历史验收见 [validation.md](validation.md)，当前操作见 [quick-start.md](quick-start.md)。
