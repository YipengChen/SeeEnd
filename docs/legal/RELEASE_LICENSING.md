# SeeEnd 0.8.4 许可处理记录

2026-10-09，发布者明确确认“按专有许可处理”。SeeEnd 原创代码从 0.8.4 起使用根目录 LICENSE 中的专有许可。原始仓库保持私有，不将原创源码发布到资料仓库。此前 GPL 许可文本保存在 LEGACY_GPL_3_0.txt，已有授权不撤销。

公开分发内容：桌面二进制安装包、隐私政策、安装说明、许可文本和第三方源码材料。可读运行脚本并不表示开源授权。

AI Flame 设备移除后，当前构建不再分发 esptool、mpremote、pyserial 或专用固件。Qt/PySide 与相关 LGPL 库保持动态链接，提供对应上游源码、Anaconda 构建配方和补丁、许可文本及库替换说明。

prepare_licenses.py 依据上游 SHA256 核对源码包。third_party_sources/ 和 packaging/generated_licenses/ 是生成材料，不包含 SeeEnd 源码仓库。资料仓库采用明确文件白名单上传。

发布前检查：原仓库仍为私有；桌面模块归档不包含已移除的硬件模块或 esptool/mpremote/serial/readline；实际打包的 Anaconda 动态库发行组件都有许可材料；本机桥接生命周期验证；分发归档和源码材料匹配。原 0.8.3 GPL 构建不得套用新专有许可重新发布。
