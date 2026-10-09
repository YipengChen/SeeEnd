# 项目工作规则

## 修改代码后重新打包

- 每次涉及代码修改（包括界面、文案、样式和资源）的任务，完成修改和必要检查后，必须重新打包，不能只交付源码修改。
- 使用项目虚拟环境运行 `.venv/bin/python packaging/build.py`，再运行 `.venv/bin/python packaging/verify_frozen.py` 验证打包产物，并运行 `.venv/bin/python packaging/archive.py` 生成分发压缩包。
- 默认构建当前机器的 macOS Apple 芯片版本，产物放在 `dist/`；用户明确指定其他平台时遵循用户要求。
- 最终回复提供最新压缩包的可点击路径；如打包或验证失败，应修复并重试，仍无法完成时如实说明原因。
- 用户已授权此流程，后续代码修改完成后无需再次询问是否重新打包。
