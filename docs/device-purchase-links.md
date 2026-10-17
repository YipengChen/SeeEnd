# 设备建议和购买链接配置

统一修改 `aiflame/devices/purchase.py` 中的 `DEVICE_PURCHASES`。每个键对应一个设备型号，名称与 `aiflame/devices/models.py` 的 `id` 一致。

```python
"yeelight-cube-lite": {
    "advice": "适合桌面近看，点阵状态一眼可辨",
    "advice_en": "Readable pixel status for a nearby desk display",
    "taobao": "https://example.com/seeend/yeelight-cube-lite/taobao",
    "jd": "https://example.com/seeend/yeelight-cube-lite/jd",
},
```

- `advice`：中文使用建议，保持简短；卡片始终单行显示，窄窗口省略的部分可悬停查看。
- `advice_en`：英文使用建议；留空则沿用中文建议。
- `taobao`、`jd`：该型号的商品或推广链接，填写完整链接即可。删除字段或设为空字符串 `""` 可隐藏对应平台入口。
- 图标按该设备条目中 `taobao`、`jd` 字段出现的先后顺序从左到右显示；将 `jd` 放在前面即可优先显示京东。

平台图标位于 `aiflame/assets/brands/taobao.svg` 和 `jd.svg`。图标独立打开浏览器，不触发设备接入。页面底部文案为“购买前请在商品页核对型号”。

修改后重启源码运行的应用即可查看；已打包的应用需要重新构建。按顺序执行：

```sh
.venv/bin/python packaging/build.py
.venv/bin/python packaging/verify_frozen.py
.venv/bin/python packaging/archive.py
```
