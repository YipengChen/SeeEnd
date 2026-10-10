# SeeEnd Chrome 应用商店提交材料

> 本目录的 0.2.8 ZIP、截图、拒绝和操作记录保留历史提交证据。当前扩展 0.2.10 的新提交包由 packaging/extension_release.py 生成到 dist/；本地生成不表示已重新提交或通过审核。

本套材料用于 SeeEnd 浏览器扩展 0.2.8 的商店草稿上传、文案填写、隐私页面托管和审核准备。extension-upload.zip 可用于首次上传创建草稿；正式提交审核前，必须完成商店 ID 对接、填写发布者信息并公开托管下载和隐私页面。

## 文件用途

| 文件 | 用途 |
| --- | --- |
| extension-upload.zip | 上传开发者后台，manifest.json 位于 ZIP 根目录；仅包含扩展资源 |
| STORE_LISTING_ZH.md 和 STORE_LISTING_EN.md | 中文和英文商店文案 |
| PRIVACY_FIELDS.md | 单一用途、权限理由、远程代码和数据类别填写说明 |
| privacy/index.html | 可直接托管的中英文隐私政策网页 |
| privacy/PRIVACY_POLICY_ZH.md 和 PRIVACY_POLICY_EN.md | 隐私政策可编辑源文案 |
| assets/store-icon-128.png | 128×128 商店图标 |
| assets/screenshot-01-1280x800.png | 新版扩展实际完成状态截图 |
| assets/screenshot-02-1280x800.png | 真实桌面配套界面截图 |
| assets/promo-small-440x280.png | 440×280 宣传图 |
| assets/promo-marquee-1400x560.png | 1400×560 可选宣传图 |
| review/TEST_INSTRUCTIONS_EN.md | 提供给审核员的英文操作指南 |
| STORE_ID_SETUP.md | 首次上传后对接扩展 ID 的步骤 |
| SUBMISSION_CHECKLIST.md | 提交清单与待填写信息 |
| FILES_SHA256.json | 当前本地材料校验值，不代表商店已接收这些修订 |

## 填写和发布

先阅读 SUBMISSION_CHECKLIST.md。发布者已确认 Yipeng Chen 和公开邮箱 ypchenmail@gmail.com，邮箱已通过后台验证。下载、资料仓库和隐私政策地址已补齐。官网是选填项，隐私页面和审核所需桌面下载地址必须可访问。

privacy/index.html 为静态页面，不依赖脚本或外部字体；可放在官网或静态托管平台。2026-10-09 已公开托管到 https://yipengchen.github.io/SeeEnd-downloads/ ，后台填写结果见 APPLICATION_STATUS.md。

截图使用 2026 年 10 月 9 日真实扩展弹窗和 SeeEnd 网页接入界面，保留实际状态，没有合成“已完成”记录。界面不是第三方 AI 平台官方产品；不包含聊天正文、凭据或个人资料。宣传图使用项目既有 SeeEnd 图标。

本地桌面产物：SeeEnd-0.8.4-Darwin-arm64.zip。商店 ID 已对接；后续改动应重新发布并验证匹配 ID 的产物，不要直接把本地路径填写给审核员。

## 官方要求

[注册开发者账号](https://developer.chrome.com/docs/webstore/register/)、[发布流程](https://developer.chrome.com/docs/webstore/publish)、[图片要求](https://developer.chrome.com/docs/webstore/images)、[隐私字段](https://developer.chrome.com/docs/webstore/cws-dashboard-privacy)、[审核测试说明](https://developer.chrome.com/docs/webstore/cws-dashboard-test-instructions)。

支持页：https://yipengchen.github.io/SeeEnd-downloads/support.html 。

商店操作与公开下载情况以日期化记录为准；本地重新打包不会自动更新公开安装包。旧截图保留当时的提交证据。
