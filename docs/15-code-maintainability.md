# 代码可维护性与双语注释

用户新增要求：面向对象、职责清楚、重复功能复用、适当中英文注释。仓库开发约定见 [AGENTS.md](../AGENTS.md)。这些要求同时用于后续功能与既有代码整改，不把新增约定文件当作全仓重构完成。

服务负责用例协调；存储类及按领域划分的 mixin 负责事务；原生页面/对话框负责显示和信号；工作进程负责受限的解析或网络操作。纯格式转换可保留无副作用函数，避免为了形式上的类封装引入重复状态。界面不另写一套业务校验或 SQL。

本轮以不可变文本校验策略复用高级搜索与全文搜索的字节上限、单行控制字符和编码边界。元数据、索引事务、姓名语义及原生异步生命周期补充双语说明；重点方法改成易读的多行代码。测试验证重构前后的行为及错误边界，接口和数据库版本不因整理代码额外改变。

注释解释原因和限制，不复述代码；机器识别的 SPDX、协议字段、用户原文及测试素材不翻译。面向用户的提示继续采用中文。双语示例：

```python
# 保留旧有效索引，失败重解析不能伪称成功。
# Preserve the valid index; a failed reparse must not appear successful.
```

后续逐模块复核：服务和存储大方法、旧页面的密集单行、路径/导出/凭据边界、引擎及网络 worker 注释。只在有明确职责或重复证据时拆分，配合失败路径与原生回归；既有代码的全量可维护性和双语注释检查尚未完成。


回收站增量提取 ManagedIODialog：全文检索和回收站共用主窗口 IO、延迟串行操作、关闭与晚回调处理。TrashLibrary 集中管理回收状态与清理事务；纯文本确认 helper 复用两种删除提示，避免元数据被当作 HTML。新增关键生命周期与清理身份不变量提供双语说明；旧代码全仓复核仍待完成。


重复候选与合并由 DuplicateLibrary 维护校验、关系迁移及事务，原生面板只选择来源和展示结果，继续复用 ManagedIODialog 与纯文本确认。Service 复用相同范围校验和在途进程检查，不重新实现领域规则；来源快照与逐行revision摘要分别处理，避免复制大块IR。新增关键归属、失败、预算和隐私不变量使用中英文说明。

无文件增量提取 BibliographicPolicy，统一真实 PDF 能力与创建/修改元数据校验。AttachmentLibrary 管理主要文件归属，页面只使用服务给出的真实 ID；新建书目对话框复用 ManagedIODialog，作者和出版字段继续使用既有编辑器。迁移、隐藏主要文件、永久删除选择和无文件合并关系的关键原因提供双语注释。旧页面密集单行与全仓注释复核仍未完成。

引文导入由CitationImportPolicy共享输入/日期/元数据限制、CitationImporter管理转换/可信预览/进程，存储层复用单条与批量新书目构造/插入。BibTeX语法仍由固定成熟解析器承担，StrictBibTexParser只约束重复字段归并并校验兼容性，不另写一套语法。UI复用ManagedIODialog与完整作者展示，Python启动及冻结入口分流spawn；不把纯文本格式转换、IO监督和SQL事务混在页面中。预算、关闭、损失和安全拒绝的关键原因补充中英文说明。


## Zotero 迁移与导入导出入口

新增 SnapshotReader/Planner/Importer/PdfValidator/Library 分离来源只读复制、纯字段适配、可信预览生命周期、隔离PDF校验和本地事务。Native ImportExportDialog复用现有业务；ZoteroMigrationDialog分页核对并通过ManagedIODialog管理共享IO，不在界面重做元数据规则。未适配字段和批注原始档案不转换成已验证证据；注释记录路径、取消、WAL和事务不变量并保持中英说明。191项全套及原生新旧流程通过，完整文件模块进一步拆分、真实资料库及安装验收仍待执行。

资源迁移由 ZoteroResourcePlanner 复用只读 SnapshotReader 与隔离 PDF 校验，MigrationRecoverySession 统一日志、发布和恢复；UI只传明确目录与资源身份，预览、提交、导出共享边界。未适配的原生批注及链接HTML周边继续单列，失败与未知身份不作成功清理。

## 分支整合补充（2026-10-04）

评分持久化归 `ScoringLibrary`；桌面、CLI、MCP 共用 `AgentWorkbenchService`。元数据类型名与合法字段集中在 metadata/BibliographicPolicy。模型地址共用 model_endpoint；有界子进程输出与 stdin、总超时、进程树收尾共用 run_captured。arXiv 和评阅对话框使用 ManagedIODialog，不各自维护关窗线程策略。新增生命周期与隐私不变量采用中英文说明；命令行参数转换保持纯函数，IO 经服务边界。
