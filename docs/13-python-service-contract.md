# Python 原生桌面服务合同

用户选择全 Python 实现后，本合同替代旧 Rust/Tauri IPC 合同。PySide6 直接调用 `polyscholar.service.LocalService`，没有浏览器、WebView、HTTP 服务或团队账户。数据与结果保存本机。引擎运行时使用随应用提供的两份独立 CPython，用户无需安装或配置 Python。

## 调用接口

`LocalService(data_dir=None, resources_dir=None)`。默认资料目录采用各平台标准应用数据目录；打包资源位于 macOS `Contents/Resources/resources`，Windows/Linux `_MEIPASS/resources`。开发时使用工程资源根目录。

- `list_documents() -> list[dict]`
- `import_pdf(path) -> dict`：100 MiB 限制、PDF 文件头验证、SHA-256 去重、只读本地对象副本。
- `update_document(document_id, patch) -> dict`：标题、作者、DOI、年份、标签和本地笔记。
- `delete_document(document_id)`：保护正在执行的任务；删除库中副本与该文献任务产物，保留外部原文件。
- `read_pdf(document_id) -> bytes`：供 QtPdf 的 QBuffer 使用。
- `get_settings() -> dict`；`save_settings(settings) -> dict`。
- `set_session_key(key)`：密钥仅存进程内存，传空文本清除，关闭应用清除；不写 JSON、SQLite、日志。
- `discover_engine(engine) -> {pythonPath, available, message}`：只查应用 `runtime/<engine>/bin/python3` 或开发 `.runtime/<engine>`，Windows 查 `python.exe`/`Scripts/python.exe`；独立验证 BabelDOC 0.6.4、PDFMathTranslate 1.9.11。不存在时明确报告，不回退到系统 Python。
- `list_models(endpoint=None, key=None) -> list[str]`：使用会话密钥 GET OpenAI 兼容 `<base>/models`，超时 15 秒、响应最多 1 MiB、禁止重定向凭据；去重排序，失败只显示脱敏提示，允许手动填写模型名。GUI 应在 QThread 调用。
- `list_jobs() -> list[dict]`
- `start_translation(document_id, pages='') -> dict`：自动选择所属引擎内置解释器，后台线程执行 `python -I integrations/job_worker.py`，密钥经 stdin 传入；默认已授权 API 与模型/字体下载，无额外勾选。首版全局一个活动任务。
- `read_artifact_pdf(job_id, artifact_index=0) -> bytes`
- `export_translation(job_id, artifact_index, path)`：仅可复制已完成任务的真实 PDF；拒绝产物路径穿越、内部译文覆写、源文献对象库覆写及硬链接别名；目标由原生保存对话框选择。
- `export_metadata(document_ids, format, path)`：`document_ids` 可为单个 ID 或 ID 列表；格式为 `csl-json`、`bibtex`、`ris`。属于元数据交换，不承诺 GB/APA 等最终排版。
- `close()`：清除密钥、终止活动 worker 并等待回收；远程已发送请求可能计费。GUI 退出时调用。

文献字典字段：`id,title,authors,doi,year,tags,notes,sha256,filename,sizeBytes,createdAt`。作者使用分号分隔，`tags` 为字符串列表，时间为 UTC ISO8601。

设置字段：`endpoint,model,engine,pythonPath,cachePath,sourceLanguage,targetLanguage,doiEnabled,timeoutSeconds`。`pythonPath` 只用于兼容旧设置/运行状态，实际引擎路径受应用控制，无用户安装要求。模型名默认为空，通过服务查询或手动填写。拒绝设置中出现额外字段或 API 密钥；endpoint 拒绝凭据、query、fragment，要求 HTTPS 或本机环回 HTTP。

任务字段：`id,documentId,engine,state,createdAt,error,artifacts,outputDir,timeoutSeconds`；状态为 queued/running/completed/failed。输出目录固定存入任务记录，缓存变更只影响新任务；旧任务结果仍可读。缓存目录必须为本机绝对路径、可创建且可写，并且在源文献对象库之外。

本地追踪仅记录事件类别、结果和时间；不回传、不包含论文正文或密钥。无关键词文献搜索、代写、科学真伪判断、多人 SaaS 或云同步。

## 集合与标签

集合从数据库 v2 引入：`desktop_collections` 自引用父集合；`desktop_memberships` 为文献/集合多对多关系。v1 升级前使用 SQLite backup 保存 `library-before-v2.sqlite3`，原文对象不迁移。

服务新增 `list_collections/create_collection/update_collection/delete_collection`、`document_collections/set_membership`、`list_tags/rename_tag` 与 `search_documents`。集合删除级联子集合和成员关系，文献/附件保留；成员移除只删除关系。树移动拒绝循环。搜索支持 text、collection_id、unfiled、tags（交集）、include_descendants。

## 自查整改后的合同

- 同一资料目录同时只允许一个 `LocalStore`，OS 锁先于迁移和恢复获取；调用者必须 `close()`，服务等待工作线程收尾后释放锁。锁文件可保留，是否有活跃 OS 锁才代表正在运行。
- 缓存选择保留原路径，仅将应用拥有的 `jobs` 子目录设为 POSIX 0700；不修改用户共享父目录权限。Windows ACL 仍待验收。
- `timeoutSeconds` 是 1–86400 的整数，默认为 600；每个任务保存提交时的值。超时会终止本地进程，远程请求仍可能计费。
- `format_metadata(ids, format)` 供预览与导出共用；仅导出用户选中条目。
- `subscribe_jobs(callback)` 返回取消订阅函数，通知在工作线程发出，原生 UI 通过 queued Qt 信号接收。仅接受实际提供的百分比和 token 用量；未知费用保持未知。
- `diagnostic_report()` 只包含版本、平台、引擎/状态、允许的错误码与超时；不含路径、文献、密钥、端点或原始日志。用户预览后可 `export_diagnostics(path)` 保存本机，无自动上报。
- 导出经临时文件和原子替换完成，保护原始导入文件、内部文献库/资源/译文及其硬链接；异常统一为本地可理解文案。
- SQLite v3 升级前备份，审计新写入由枚举和触发器校验，保留旧历史。

## DocumentIR 与证据笔记（v4）

`parse_document(id)` 在有界本地子进程中提取真实 PDF 原生文字，保存新解析版本；不调用模型、不执行 OCR。源码运行安装 PyMuPDF 1.28.2；冻结发行使用包内受控 CPython，不退回系统解释器。

`current_document_ir(id)`、`document_blocks(id, revision_id=None)` 返回实际版本、页码、文本及可见页面坐标。`save_claim(id, text, evidence)` 校验逐字摘录和文献/版本关系；`list_claims(id)` 返回关联/无证据及 stale 状态。重新解析不复用旧块 ID，也不将旧摘录自动绑定新版本。v4 升级前保存 `library-before-v4.sqlite3`。

本地人工笔记不等于模型摘要，不判定科学结论真伪。解析版本/页面/块/结论/证据采用关系表与组合外键，文献基础元数据仍部分使用 JSON。


## 选段模型摘要（v5）

`summarize_document(id, block_ids, revision_id=None, expected_settings=None)` 将显式选择的当前文本块发送至配置的 OpenAI-compatible `/chat/completions`，使用会话密钥及 JSON 模式；不上传 PDF、图片、标题或未选正文。界面展示实际端点、模型、目标语言和完整选段；配置变化时拒绝沿用旧展示范围发起请求。

单次最多 64 块、128 KiB 原文，输出预算 4096 tokens，响应最多 1 MiB。失败不自动重试或改换提供商。设置超时最多取 120 秒；受监督的 Python 网络工作进程覆盖连接、响应头和正文的总时限，到期终止并回收。密钥通过 stdin 传递，关闭服务会终止在途工作进程。冻结包使用内置 BabelDOC Python，资源含独立 worker 与相邻受控辅助模块。禁止重定向，错误不包含原始响应。

每项包含 `text,category,attribution,evidence`。类别为 question/method/data/result/limitation/reproducibility/other，归属为 author_report/model_inference。引用必须是本次输入块内的逐字摘录；页码由本地数据生成。无引用的条目标记缺少证据。输入版本改变、伪造来源或任一条目无效时整批不保存。

SQLite v5 在升级前备份 `library-before-v5.sqlite3`，保存批次模型、实际有效 token 用量、输入块 ID、生成时间以及类别/归属。手写笔记与模型摘要分开标记，重解析后旧结果保留并提示引用失效。模型归属标签本身仍需人工核对。


## 托管多 PDF 附件（v6）

`list_documents/search_documents` 返回主条目，内部 `LocalStore.list_documents` 仍返回所有 PDF 以保留去重与导出保护。`list_attachments(parent_id)` 返回原始 PDF 和子附件，其 `id/documentId` 均为真实 PDF 内容身份，另有 `parentDocumentId,role,filename,label,sha256`。原始 PDF role=original，新增支持 supplement/translation。

`import_attachment(parent_id,path,role='supplement')` 原子建立子文献和附件关系；不增加文献库主条目。相同归属重复导入保留原角色及 ID，另一归属拒绝。普通 `import_pdf` 重复导入隐藏子附件会返回所属主条目。

`delete_attachment(parent_id,document_id)` 仅删除额外附件；根 PDF 必须通过 `delete_document` 删除整个条目。两种删除均检查相应子任务，且删除整个条目检查所有子附件；保留外部原文件，清理拥有的对象、任务与证据。集合和原生书目引用列表只使用根条目。读取、解析、模型摘要和翻译依旧接受对应 PDF 的独立 ID，不将子附件内容与主 PDF 混用。

v6 升级前备份 `library-before-v6.sqlite3`。当前不支持外部链接、非 PDF 附件或主 PDF 替换。
