# SeeEnd 商店扩展 ID 对接

商店 ID：`miokmdeomlchjpihkcnommpnheagoimc`。公钥由后台“文件包 → 查看公钥”取得，保存在 store-public-key.txt。已验证公钥哈希与 Item ID 一致，并同步源码 manifest.json 的 key。

桌面桥接允许商店 ID 及原开发 ID `hifannlfdbbhmmgecknoakpbhfihihbg`，用于迁移兼容，不含通配符。原有注册必须使用相同宿主名称、stdio 类型、SeeEnd 启动路径和已知来源，才允许更新；宿主本身也校验调用来源。来源或路径不符的同名配置会拒绝修改。

新桌面应用已重新构建，11 项桥接测试及打包产物生命周期验证通过。当前应用请在「应用接入 → 安装浏览器扩展」按四步引导操作；第 4 步「连接并验证网站」先选网站，再允许连接、开启提醒，并通过「在 Chrome 打开」开始新任务验证。

商店上传包 extension-upload.zip 不含 key，符合后台要求。应用内提供的开发加载包保留商店公钥，可取得与商店一致的 ID。当前 dist/SeeEnd-Web-extension 是浏览器已加载的原开发版本，暂时保留其 ID。

提交审核前仍需：公开托管匹配商店 ID 的桌面应用，提供隐私政策网址，完成商店 ID 开发加载或商店安装后的真实 working → done 和停止取消验证。当时的提交与审核记录见 APPLICATION_STATUS.md；当前入口尚未确认可以直接安装。

公钥和 ID 的官方流程：[保持一致的扩展 ID](https://developer.chrome.com/docs/extensions/reference/manifest/key)。

应用现在直接准备固定的扩展目录，无需用户下载和解压；操作图文教程在桌面 App 内打开，使用真实 App 与 Chrome 截图，不依赖网页版控制台。扩展安装目录为独立的 SeeEnd/BrowserExtension。ZIP 下载接口保留用于开发和兼容用途。
