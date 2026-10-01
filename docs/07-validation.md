# 验证记录

范围更新：2026-10-02，允许模型 API、DOI 元数据查询及模型/字体下载，其余业务处理与保存本地完成。新增 docs/09-local-and-network-scope.md；本轮为需求/架构文档修改，已通过本地链接与 SQLite 基线检查，没有新增或声称完成运行时联网隔离。

日期：2026-10-02；本机 macOS，Python 3.9/3.12。

## 已验证

- BabelDOC 0.6.4 与 PDFMathTranslate/pdf2zh 1.9.11 已安装到两个独立 Python 3.12 环境，环境目录被 Git 忽略。
- 实际 CLI `--version` 分别返回 `main.py 0.6.4` 与 `pdf2zh v1.9.11`。
- 两个环境 `pip check` 通过。实际运行发现新版腾讯 TMT SDK 删除了旧引擎需要的导入，已为 pdf2zh 固定 `tencentcloud-sdk-python-tmt==3.1.70` 并重新验证 CLI。
- `scripts/smoke_engines.py` 对真实已安装引擎执行版本和参数解析测试：BabelDOC 确认临时 TOML 的模型与合成 token 被正确读取；pdf2zh 确认 OpenAI service、语言、配置和页范围参数。没有发起翻译或付费请求。
- SQLite 初始迁移、外键/状态/JSON 约束、Markdown 本地链接基线检查通过。
- Python 适配测试覆盖外发授权、端点/页范围校验、密钥不进 argv 或父环境、两个引擎合成子进程输出、超时和配置清理；虚拟环境 Python 路径保留，避免解析符号链接后落入系统解释器。
- 本地工具可生成并核对未签名源码哈希清单；它不证明官方来源或授权。

## Rust 与平台验证

本机无 cargo/rustc，未本地运行 Rust。GitHub CI 配置对 Windows、macOS、Ubuntu 运行核心测试与 Python 检查。实际 CI 结果以仓库 Actions 为准；核心 CI 不能替代桌面安装/端到端验证。

## 未验证 / 未实现

真实付费 LLM、合法论文全文翻译质量、图文 OCR 质量、严格预算网关、真实断点续传、桌面安装包与界面、引用处理器、Zotero 同步、发行签名与完整 SBOM、Windows 临时凭据 ACL、异常崩溃后的清理、桌面源码与许可入口。

产品范围明确为个人本地桌面，不采用 B/S；没有 Web/PWA/团队服务。合成子进程与真实参数解析测试不等同于上游真实全文翻译，也不证明实际模型或上游下载行为满足隐私承诺。
