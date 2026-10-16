# SeeEnd 商店扩展 ID 对接

商店 ID 为 `miokmdeomlchjpihkcnommpnheagoimc`，公钥保存在 `store-public-key.txt`，源码 manifest 的 key 用于保持开发加载时的 ID 一致。商店格式 ZIP 移除 key；版本化开发包保留它。

桌面桥接还兼容旧开发 ID `hifannlfdbbhmmgecknoakpbhfihihbg`，用于迁移，不使用通配符。更新已注册桥接时，宿主名称、stdio 类型、应用路径与允许来源均须符合归属检查；同名配置不属于 SeeEnd 时拒绝覆盖。

用户按 [使用指南](../quick-start.md) 的商店引导操作。开发验收运行 `packaging/extension_release.py`，在 `chrome://extensions/` 加载生成的版本化扩展目录；重新加载后刷新网页、连接桥接并发起一条新任务。不要删除仍被浏览器加载的目录。旧 `SeeEnd/BrowserExtension` 和 `dist/SeeEnd-Web-extension` 属历史安装位置，不能作为新版本的固定路径。

当前源码扩展为 0.2.13；本地生成、公开上传、商店提交和审核通过是不同阶段。2026-10-16 后台已核对草稿 0.2.13、已发布 0.2.12，详细结果按 [日期化记录](APPLICATION_STATUS.md) 确认。
