# main 与 feat/agent-scoring 整合

2026-10-04；基线 main `26695b9`，功能分支原 head `5733cb1`。

| 范围 | main 原有能力 | 功能分支新增 | 整合结果 |
|---|---|---|---|
| 文献库 | 无文件书目、主要 PDF、附件、回收站、重复合并 | 评分列、arXiv 导入 | 全部保留，重复远程导入仅补空字段 |
| 翻译 | BabelDOC、PDFMathTranslate、内置运行时、内存凭据 | arXiv HTML | PDF 默认入口保留；HTML 显式选择并独立托管 |
| 摘要 | 精确摘录、证据、原文跳转、模型来源 | Agent 提炼与原始评阅 | 并存；评阅不代表科学结论真伪 |
| 交换 | Zotero 目录迁移、BibTeX/RIS/CSL-JSON | CLI/MCP | 共用本地服务、校验、导出保护及单实例锁 |
| 数据库 | v12：书目/Zotero 结构 | v12：评分结构 | v13 非破坏兼容迁移，不删除证据表 |
| UI | 原生 QtWidgets/QtPdf | 评分明细与 arXiv 对话框 | 多列排序、结构化明细、键盘入口、受管 IO、原生 HTML 阅读 |

main 有 32 个独有提交；功能分支有 3 个。合并保留双方历史，不替换 main 的安全引擎、摘要、迁移或冻结验证代码。

## 兼容与维护

- `ScoringLibrary` 管理评分与报告；回收站保留，永久清理级联。书目合并保留源材料身份下的评阅，避免混合不同材料。
- `AgentWorkbenchService` 供桌面、CLI、MCP 共用；筛选和列表共用评分投影，PDF、元数据与集合原子导入。
- `model_endpoint`、`run_captured`、`ManagedIODialog` 分别复用地址校验、受管进程和原生 IO 生命周期；关键不变量与失败处理使用中英文说明。
- 不复制活动 SQLite/WAL/SHM，不改变默认系统资料目录。历史 feature 已删除的证据无法自动恢复；升级保留仍存在的数据。
- 密钥默认会话内存，可显式保存至系统凭据库；不启用明文保存或自动读取旧明文文件。
- CLI `--no-wait` 在创建任务前拒绝。CLI/MCP 是单用户 stdio 工具，不引入 Web 服务。

## 验证与剩余门槛

最终源码回归 390 项通过，8 项平台限定跳过；包括两种 v12 升级、原子失败回滚、受控子进程超时/关闭、stdin 凭据、MCP 实际 stdio、环回 HTTP 重定向和有界输出。基线与编译检查通过。

原生合成流程覆盖既有文献、证据摘要、无文件书目、三格式引用、Zotero 迁移与窗口关闭；新增评阅/arXiv/HTML 原生流程通过，含无 PDF 的 HTML 阅读、动态任务完成事件、快捷键与受保护导出。

这些验证不代表真实引擎、模型 API、真实 arXiv 下载或安装包验收。HTML 只缓存当前论文同源官方位图；SVG、跨域和失败图像用占位提示；Qt 富文本预览不等同浏览器完整 MathML 排版。V1–V5、Windows ACL、凭据崩溃清理、三平台干净系统安装继续作为发布门槛。


## 本轮 UI / MCP 目标验收

四个 MCP 工具：cli_docs、cli_run（兼容）、cli_execute（参数数组及结构化结果）、library_status；三个公开文档资源。连接配置、命令策略、CLI 桥接及注册分别由类负责，固定资料库、限制参数、区分库占用/参数错误，复用总超时和有界输出。可选只读模式限制命令，不宣称数据库只读或 OS 沙箱。

实际 stdio 握手验证 tools/list、resources/list/read 与 structuredContent；Windows/中文/空格路径数组、目录覆盖及缩写、非法 Unicode、只读拒绝、库占用和超时专项通过。wheel 已检查包含契约与量表资源，资源路径回归通过；这不是冻结 MCP 伴随程序的验收。

最终 390 项回归（8 平台限定跳过）及原生 UI 检查通过。GitHub 提交及 CI 结果随实际推送补记。
