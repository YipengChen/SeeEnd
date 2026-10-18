# GPL 单仓库准备记录

日期：2026-10-18。目标为完整源码公开；SeeEnd 仓库仍保持私有，由维护者在准备完成后手动切换可见性。

## 完成的结构与许可调整

- 原 SeeEnd 仓库保留提交历史；应用源码提升至本地 SeeEnd 根目录。
- 导入原 Skill 和 downloads 受 Git 管理的源码/资料，分别位于 skill/ 与 website/；忽略嵌套 Git、缓存和已构建 ZIP。导入基线：SeeEnd-skill `3ec69db`，SeeEnd-downloads `14c7582`；原仓完整历史另有仓库外 Git bundle 备份。
- 当前原创代码、Skill 和项目文档统一 GPL-3.0-only。旧发行目录保留发行时许可、资产地址和摘要。第三方品牌、组件及音效按各自许可或权利处理。
- 将维护日志、截图、模型、原视频和 Hook 备份迁至仓库外私有归档；移除缓存、重复政策 HTML 与旧商店上传 ZIP。website/ 是唯一政策网页源。
- 原私有仓库 workspace-2026-10-10 Release 附件已下载，完整解压校验和原始 SHA-256 一致后移除远端 Release，避免未来公开个人研究与界面资料。Git 源码历史不含该附件，无需为此重写历史。

## 审核证据与边界

现有所有本地 refs 与远端 refs 对齐后，扫描 40 个提交、1314 个对象、922 个 UTF-8 文本 blob，未确认真实私钥、供应商 API 密钥、长凭据字面量或设备 Token 泄漏。命中为测试样例及 Chrome 扩展公钥。此检查不是对所有历史图片像素、Actions 日志和旧安装包的完整隐私证明。

Qt/PySide、米家参考实现与猫声已有许可/来源记录。品牌图标的来源记录不构成权利方再分发授权；第三方商标与素材明确排除在本项目 GPL 授权范围之外，后续对外分发仍需按权利方要求使用。

新 CI 在 GitHub 构建三个平台，全部成功后才从匹配 tag 创建草稿 Release；上传平台包、对应第三方源码、完整原始 SeeEnd 源码、独立 Skill 和收据，并校验 SHA256SUMS。Windows 与 Unix 的源码指纹改为按相对 POSIX 路径排序，避免 Path 平台排序规则不一致。

修复已有 Linux CI 失败：英文窄窗口中微信设置标题、按钮与开关同排导致横向溢出，按钮改用独立行，新增字形宽度边界回归。构建与测试结果以本次 Actions 实际状态为准；自动测试不替代目标系统签名/安装、真实浏览器、系统通知和实体设备验收。

固定直接依赖的 PyPI 安全公告检查发现 cryptography 49.0.0 有 CVE-2026-69247，影响 PKCS#7 EnvelopedData 解密。SeeEnd 使用 AES CBC/GCM，未调用受影响接口；依赖仍升级至已修复的 50.0.2。其余固定直接依赖在本次检查未列出安全公告，此结果不涵盖全部传递依赖或未来公告。依据：[cryptography 官方变更记录](https://cryptography.io/en/latest/changelog/)。构建矩阵沿用已有的 macOS Apple 芯片、Windows x64、Linux x64 范围，不在本次仓库整理中新增 Intel Mac 发行支持。macOS runner 使用 macos-15，避开即将下线的 macos-14。
