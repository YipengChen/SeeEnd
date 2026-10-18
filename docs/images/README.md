# README 应用截图

`overview-zh.png` 和 `overview-en.png` 是当前应用「总览」界面的中英文截图；`supported-devices-zh.png` 和 `supported-devices-en.png` 展示「我的设备」中可选的六类设备。总览截图使用公开的演示任务数据，不包含个人任务、路径或账户信息，不代表真实任务或设备验收记录。

截图直接由 `Dashboard` 界面组件生成，使用临时数据库与离线 API；不启动 Hub，不读取用户配置，也不发送通知或设备指令。应用界面更新后，在仓库根目录重新生成：

```sh
python -m pip install -e ".[desktop]"
python tools/render_readme_overview.py
```

默认使用 Qt offscreen，总览界面尺寸为 1240 × 850，设备界面为 1240 × 920，均以 2 倍像素密度截图。字体使用系统回退，中文需要系统安装中文字体。截图中的品牌图标沿用应用素材，权利说明见[第三方说明](../../THIRD_PARTY_NOTICES.md)。灯效动画另见[素材说明](light-effects/README.md)。
