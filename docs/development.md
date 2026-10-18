# 开发与构建

[返回首页](../README.md) · [English](development.en.md) · [贡献指南](../CONTRIBUTING.md)

## 从源码启动

需要 Python 3.11+。App 自带本地 Hub，通常无需另开服务。

```sh
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[desktop]"
python -m aiflame
```

仅运行无界面 Hub：安装基础依赖 `python -m pip install -e .`，运行 `python -m aiflame --hub`。

安装后的 `seeend` / `seeend-hub` 对应桌面与 Hub；`ai-flame` / `ai-flame-hub` 为兼容别名。`aiflame` 包名、原用户数据目录、`AIFLAME_DATA_DIR` 及内部接入标识继续兼容已有配置。

## 测试

Python CI 使用 3.11 / 3.13。浏览器测试另需 Node.js（CI 使用 22）和 npm，App 运行本身不需要 Node.js。

```sh
python -m unittest discover -s tests -v
npm ci --ignore-scripts
npm test
python -m unittest discover -s skill/tests -v
python -m unittest discover -s tools -p 'test_*.py' -v
python .github/scripts/test_release_assets.py
python tools/check_workspace.py
```

无显示器可设置 `QT_QPA_PLATFORM=offscreen`。部分测试需要回环端口、IPC 或桌面依赖；冻结 Helper 测试需相应产物。`tests/verify_*_live.py` 是手动实机工具，运行前检查目标与参数。

## 本地构建

在目标操作系统上顺序执行，需要本机 C 编译器。核心模块经 Cython 原生编译，无 Python 回退。

```sh
python -m pip install -e ".[desktop,build]"
python packaging/build.py --download-sources
python packaging/verify_frozen.py
python packaging/archive.py
```

默认产物：macOS 为 `dist/SeeEnd.app`，Windows 为 `dist/SeeEnd/SeeEnd.exe`；归档生成对应平台安装包，Linux 为 `.tar.gz`。三个命令可统一加 `--dist-dir dist/staging` 使用独立目录。首次构建下载并校验第三方材料，准备完成后可省略 `--download-sources` 离线重建。

`SeeEnd-build.json` 记录版本、源码指纹、Git 状态与第三方材料身份；源码变更后须重建，冻结验证和归档拒绝复用不匹配的 App。修改核心逻辑可先运行 `python packaging/verify_native.py`，或构建时加 `--verify-native`。

常规分发在 GitHub 构建，全部平台通过后生成草稿 Release；精简附件包含三个安装包、开发资料包及校验清单。参见[单仓库维护与发布](REPOSITORIES.md)。系统签名、公证与真实设备验收见[验证说明](validation.md)。

## 扩展与资源

`python packaging/extension_release.py` 生成版本化开发包、商店待提交 ZIP 和校验清单；生成材料不等于已提交或已发布。

品牌图标以 `aiflame/assets/icon.svg` 为源，运行 `python packaging/render_icon.py` 更新派生资源后重建。

README 灯效图从当前本地预览组件导出，生成方法和素材说明见[灯效素材](images/light-effects/README.md)。
