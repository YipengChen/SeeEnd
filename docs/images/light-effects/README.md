# README 灯效素材

五份 GIF 使用当前 `aiflame/desktop/light_controls.py` 的 `LightStateCanvas` 本地预览组件导出；颜色、亮度和白灯闪烁来自 `light_settings`，点阵帧来自 `cube_effects.frame`。

这些是程序生成的灯效示意，不是实拍，不证明实体设备的色彩、亮度、发送帧率或接入验收结果。未使用旧 AI Flame 火焰参考视频、个人配置或设备凭据，也不启动 Hub 或发送设备指令。

| 文件 | 内容 | 循环时长 |
| --- | --- | --- |
| lights.gif | 挂灯、W3、Beam：先彩灯三态，再白灯三态 | 18 秒 |
| cube-status.gif | 像素状态牌：跳点、问号、对勾 | 9 秒 |
| cube-comet.gif | 彗星巡航：往返、两端交替、中心展开 | 9 秒 |
| cube-snake.gif | 贪吃蛇：工作、原地等待、完成 WIN | 9 秒 |
| cube-text.gif | 文字状态：WORK、WAIT、DONE | 9 秒 |

每种状态展示三秒，底部标明当前状态。默认彩灯为工作黄、等待红、完成绿；颜色和亮度均使用应用默认值。Cube 的四种点阵为可选模式，图中不代表上电默认模式。白灯等待按默认 600ms 亮 / 600ms 灭显示。

## 重新生成

在仓库根目录安装桌面依赖及文档导出所需的 Pillow，然后执行：

```sh
python -m pip install -e ".[desktop]" Pillow
QT_QPA_PLATFORM=offscreen python tools/render_readme_media.py
```

脚本默认设置 offscreen，因此 Windows 也可直接运行 `python tools/render_readme_media.py`。动画以 10 FPS 采样，静止帧由 GIF 编码合并，不改变展示时长。

导出脚本与原创渲染逻辑遵循根目录 GPL-3.0-only。灯具 SVG 沿用应用现有素材，其来源、商标及第三方权利边界仍见[第三方说明](../../../THIRD_PARTY_NOTICES.md)；示意图不增加对第三方商标或产品外观的授权。
