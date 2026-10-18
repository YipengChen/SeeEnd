# README 灯效素材

中文 `zh/`、英文 `en/` 各九种动画，分别供中英文 README 使用。每种动画导出 `light/` 深色文字与 `dark/` 浅色文字两份透明 WebP，README 用 `<picture>` 按页面明暗选择。图片从当前 `aiflame/desktop/light_controls.py` 的 `LightStateCanvas` 本地预览组件导出；点阵帧来自 `cube_effects.frame`。

这些是程序生成的灯效示意，不是实拍，不证明实体设备的色彩、亮度、发送帧率或接入验收结果。未使用旧 AI Flame 火焰参考视频、个人配置或设备凭据，也不启动 Hub 或发送设备指令。

| 每种语言中的文件 | 内容 | 循环时长 |
| --- | --- | --- |
| screen-light-bar.webp | 屏幕挂灯 Pro：彩灯三态、白灯三态 | 7.2 秒 |
| w3-bulb.webp | W3 灯泡：彩灯三态、白灯三态 | 7.2 秒 |
| beam.webp | Beam 塔灯：彩灯三态、白灯三态 | 7.2 秒 |
| mijia-plug-3.webp | 插座 3（ZNCZ301KK）控制外接台灯 | 3.6 秒 |
| mijia-plug-4.webp | 插座 4（ZNCZ401KK）控制外接台灯 | 3.6 秒 |
| cube-status.webp | 像素状态牌：跳点、问号、对勾 | 3.6 秒 |
| cube-comet.webp | 彗星巡航：往返、两端交替、中心展开 | 3.6 秒 |
| cube-snake.webp | 贪吃蛇：工作、原地等待、完成 WIN | 3.6 秒 |
| cube-text.webp | 文字状态：WORK、WAIT、DONE | 3.6 秒 |

每种状态展示 1.2 秒（原为 3 秒），动画本身的移动与闪烁速度不变。底部将灯效与任务状态放在同一行，间隔 24 像素：中文为“灯效：彩灯　　任务状态：工作中”，英文使用“Effect:”与“Task status:”前缀。字号和两段文字的位置固定，避免切换状态时跳动。文字无底色，随页面明暗使用固定黑色或白色，颜色变化仅体现在灯光中。点阵里的 WORK / WAIT / DONE / WIN 是应用实际使用的灯效图案，保持与实际输出一致。

## 可见度与参数

所有图片使用真正的透明背景，保留灯光渐隐与抗锯齿的半透明边缘；彩灯、白灯的三个状态使用 100% 展示亮度，配色仍为工作黄、等待红、完成绿。仅文档生成脚本使用这些高亮参数，应用默认参数不变。Cube 的四种点阵为可选模式，不代表上电默认模式；白灯等待仍按默认 600ms 亮 / 600ms 灭显示。

插座使用原有通断电规则：工作断电，等待输入和完成通电，图中的台灯用于展示外接灯具。插座不展示彩色变光或继电器快速闪烁。插座图的首帧选择等待输入的通电状态，循环中仍完整展示三种状态。

## 自动播放

动画本身已设置无限循环。网页及账户的动画偏好可能影响播放，GitHub 提供动画自动播放的辅助功能设置。

在 [GitHub Settings → Accessibility](https://github.com/settings/accessibility) 的 Motion 中，将 **Autoplay animated images** 设为 **Enabled** 即可。见 [GitHub 官方说明](https://docs.github.com/en/account-and-profile/how-tos/account-settings/managing-accessibility-settings)。README 不修改访客账户的辅助功能偏好。

## 重新生成

在仓库根目录安装桌面依赖及文档导出所需的 Pillow，然后执行：

```sh
python -m pip install -e ".[desktop]" Pillow
QT_QPA_PLATFORM=offscreen python tools/render_readme_media.py
```

脚本默认设置 offscreen，因此 Windows 也可直接运行 `python tools/render_readme_media.py`。中文字体使用系统字体回退，建议安装苹方、微软雅黑或 Noto Sans CJK。动画以 10 FPS 采样，每个状态 12 帧，静止帧由编码器合并，不改变展示时长。使用无损动画 WebP 保留完整 alpha，避免 GIF 的单级透明损伤灯光边缘。

导出脚本与原创渲染逻辑遵循根目录 GPL-3.0-only。灯具 SVG 沿用应用现有素材，其来源、商标及第三方权利边界仍见[第三方说明](../../../THIRD_PARTY_NOTICES.md)；示意图不增加对第三方商标或产品外观的授权。

格式与主题支持：[GitHub WebP 支持](https://github.blog/changelog/2025-08-28-added-support-for-webp-images/) · [按主题选择图片](https://github.blog/changelog/2022-08-15-specify-theme-context-for-images-in-markdown-ga/)。
