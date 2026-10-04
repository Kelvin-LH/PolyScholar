# Python 原生桌面服务合同

用户选择全 Python 实现后，本合同替代旧 Rust/Tauri IPC 合同。PySide6 直接调用 `polyscholar.service.LocalService`，没有浏览器、WebView、HTTP 服务或团队账户。数据与结果保存本机。引擎运行时使用随应用提供的两份独立 CPython，用户无需安装或配置 Python。

## 调用接口

`LocalService(data_dir=None, resources_dir=None)`。默认资料目录采用各平台标准应用数据目录；打包资源位于 macOS `Contents/Resources/resources`，Windows/Linux `_MEIPASS/resources`。开发时使用工程资源根目录。

- `list_documents() -> list[dict]`
- `import_pdf(path) -> dict`：100 MiB 限制、PDF 文件头验证、SHA-256 去重、只读本地对象副本。
- `update_document(document_id, patch) -> dict`：标题、作者、DOI、年份、标签和本地笔记。
- `delete_document(document_id)`：保护正在执行的任务，默认移入回收站；显式永久删除才清理副本与产物，保留外部原文件。
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

`delete_attachment(parent_id,document_id)` 仅删除额外附件；根 PDF 必须通过 `delete_document` 删除整个条目。两种删除均检查相应子任务，且删除整个条目检查所有子附件。当前 v9 的默认删除改为移入回收站；永久清理须显式调用下述 purge 接口。集合和原生书目引用列表只使用根条目。读取、解析、模型摘要和翻译依旧接受对应 PDF 的独立 ID，不将子附件内容与主 PDF 混用。

v6 升级前备份 `library-before-v6.sqlite3`。当前不支持外部链接、非 PDF 附件或主 PDF 替换。


## 类型化文献信息（保持 v6）

`update_document` 支持 `itemType`：article-journal、paper-conference、book、thesis。`creators` 为有序列表，含 role=author/editor、type=person/organization；个人使用 literal 或 family/given，机构仅 literal。旧作者文本只按分号分隔，保留完整姓名，不推断姓与名。普通字段编辑保留作者身份与顺序。

出版字段包含 publicationTitle、publisher、place、date、volume、issue、pages、isbn、edition、eventTitle、institution、thesisType。日期采用 ASCII YYYY / YYYY-MM / YYYY-MM-DD 并校验日历；日期与年份冲突在写入前拒绝。切换类型保留暂不适用字段，界面提示，导出仅包含当前类型适用字段。现有 v6 JSON 扩展不需要 SQL 迁移或重写文件 ID。

CSL JSON 保留类型、作者/编者角色、日期和出版字段；BibTeX 普通学位论文使用 @misc 加 type，避免推断为博士；RIS 保留 AU/A2 与类型、出版字段。导出清理控制字符及 Unicode 换行符，BibTeX 转义特殊字符；预览与保存一致。子 PDF 附件不能独立导出书目引用。RIS 不具有与本地结构化作者相同的身份语义，尚不承诺外部导入无损往返或 CSL 样式排版。字段参考 [CSL schema](https://github.com/citation-style-language/schema/blob/master/schemas/input/csl-data.json) 和 [Zotero BibTeX translator](https://github.com/zotero/translators/blob/master/BibTeX.js)。


## 高级元数据检索与保存搜索（v7）

`search_documents` 新增可选 `query={match:'all'|'any',conditions:[{field,operator,value}]}`，与既有快速搜索、集合、未分类和标签筛选取交集。字段为 title/creator/doi/year/itemType/publicationTitle/publisher/isbn/tag/notes；creator 包括个人、机构及编者的完整姓名或姓/名。文字支持 contains/not_contains/is/is_not/is_empty/is_not_empty，year 额外 before/after。Unicode casefold 比较，百分号及下划线按普通文字，未使用 SQL 通配符。多人/多标签负条件要求所有值均不匹配，空字段满足负条件；未知年份不参与数值比较。

规则限制 1–20 条，每值最多 4 KiB；未知字段/操作、空条件和值、非 ASCII 数字的年份比较在执行前拒绝。类型转换保留的元数据也可被字段条件检索。搜索不读 PDF 正文，不联网，也不运行表达式代码。

`list_saved_searches/save_saved_search(name,query,search_id=None)/delete_saved_search` 在本地保存命名规则，返回 id/name/query/createdAt/updatedAt。保存的是规则，不是结果 ID，也不自动保存当前集合/快速搜索范围；打开时动态计算并叠加界面当前筛选。规则编辑须主动更新保存项，取消不写库。SQLite v7 升级前备份 `library-before-v7.sqlite3`，新增保存搜索表，旧文献、附件、集合和证据保持。迁移的新表、审计触发器及版本号同事务；保存搜索新增/更新/删除审计与数据同事务，仅固定事件和时间。

此增量对标 [Zotero 官方高级搜索与保存搜索](https://www.zotero.org/support/searching)，尚未包含全文索引、嵌套保存规则或日期相对条件。


## 本地 PDF 全文索引（v8）

`search_fulltext(text,collection_id=None,unfiled=False,tags=None,include_descendants=False,query=None,limit=200,metadata_text='')` 检索当前 DocumentIR 文本。元数据规则先筛主条目，再检索其家族 PDF；命中保留实际 documentId/parentDocumentId/revisionId/blockId、页码及坐标，子附件不冒充主 PDF。短字及中文采用字面子串；Python casefold 后三字符以上走 SQLite FTS5 trigram 候选与 instr 精确校验，一、二字符走索引文本扫描。百分号、引号、OR 等不是表达式。返回精确 total、显式 truncated，默认最多200条、允许1–1000；每项只返回最多512字符原文摘录，不返回完整大块文本。全文 SQL 和摘录计算检查30秒预算，超时拒绝部分结果；metadata 筛选沿用现有实现，并在进入/返回全文阶段检查预算。

coverage 区分 indexed/no_text/cleared/unparsed/parse_failed；最近重解析失败且仍有有效旧版本时保留索引，并显示 lastParseStatus=failed、previousCurrent 和固定错误码，不伪称重新解析成功。空查询仅返回覆盖状态。定位前核对当前解析版本及文本块身份。

SQLite v8 升级前备份 library-before-v8.sqlite3，回填已有当前版本；索引替换与解析版本写入同事务，删除 PDF 级联删除索引。`clear_fulltext_index/rebuild_fulltext_index(document_id=None)` 只清除/重建索引，保留 IR、原文及证据；重开不会自动恢复已主动清除的索引。重建索引不掩盖最近解析失败。解析和搜索由原生桌面受管理的工作线程执行，关闭窗口等待操作结束。

搜索完全本地，不执行 OCR 或模型调用，不处理图片中的不可提取文字。实现依据 [SQLite FTS5 trigram 文档](https://www.sqlite.org/fts5.html#the_trigram_tokenizer)。


## 回收站与持久化清理（v9）

`delete_document(id)` 仅接受根条目，`delete_attachment(parent_id,id)` 验证归属；默认都调用移入回收站。`trash_document(id)` 支持根/子 PDF 的独立标记。根标记隐式隐藏整个家族；`restore_document(id)` 仅移除指定标记，恢复根不移除此前独立删除的子标记。父条目仍在回收站时拒绝恢复子附件。保留元数据、文件、成员关系、IR、摘要及任务；已另行删除的集合不凭空恢复。

`list_trash()` 返回显式记录与 scope/restoreBlocked；正常文献、附件、任务、摘要、引用及全文范围排除失活 PDF。更新、解析、新任务、摘要及引用操作复用 active 校验。内部文献/任务列表仍保留所有记录供导出保护和清理。重复导入回收站中的同一文件要求先恢复。

`deletion_preview(id)` 返回 documentIds、counts（pdfs/notes/claims/jobs/artifacts/collections）、managedFiles、sourceFiles、explicitMarker、activeJobs、purgeAllowed。界面先预览再确认；确认文本按纯文本显示。`purge_document(id)` 只接受显式回收标记，活动任务或本机解析/摘要操作阻止删除。数据库删除、固定审计与清理计划同事务；提交之后才删除托管副本和任务目录，外部原件始终保留。

SQLite 与文件系统不能共同原子提交。返回 cleanupComplete=False 表示数据库已永久删除、仍有待清理文件，不能恢复条目，也不能显示清理成功。`list_pending_cleanup()` 和 `retry_cleanup(cleanup_id=None)` 支持重启后逐项或全部重试；固定 cleanup_failed 错误码不含原始日志。清理计划保存路径、文件/目录及父目录身份和原先是否存在，每次重试核对，拒绝链接和路径替换；重新导入后仍被数据库引用的对象保留。待清理路径继续受导出防覆盖保护。

v9 升级前备份 library-before-v9.sqlite3；回收标记、队列、审计触发器及版本号同事务迁移。原生回收站与全文检索复用 ManagedIODialog 和主窗口 IO 生命周期，没有额外线程管理器。此功能不自动清空回收站，不代替安装包或真实翻译验收。


## 重复候选与人工书目合并（v10）

`list_duplicate_candidates(limit=200)` 只读取活动根条目的候选字段，返回 items/documentIds/reasons、精确 total 和 truncated。相同类型内：规范 DOI 相同、格式/校验位有效的 ISBN 相同，或规范标题相同且完整作者身份相同、已知年份相差不超过1年。ISBN-10 转为对应978 ISBN-13；13位限定978/979。不是文件哈希去重，也不证明同一作品，不能自动合并。候选检测不联网；上限为20000根条目、64MiB候选字段、200000作者索引项、100000对和30秒检查预算，超过明确失败，不返回虚假的截断总数。ISBN格式依据 [International ISBN Agency](https://www.isbn-international.org/index.php/node/10)，不联网核查发行注册真实性。

`merge_preview(document_ids,master_id=None)` 接受2–20个不同、相同类型的活动根条目，返回规范元数据、逐字段来源选项、关联数量、activeJobs及revision。手动选择不依赖候选算法；不能把子附件当书目主项。预览最多2000个家族记录，读取事务内逐行计算关联状态摘要；IR原文不复制进历史快照。

`merge_documents(document_ids,master_id,field_sources,expected_revision)` 要求当前预览摘要匹配，拒绝关联漂移、活动任务及实际在途解析/摘要/翻译。字段来源只能取所选条目；未指定沿用主条目，日期/年份冲突拒绝。主PDF不换；其他原主PDF作为明确标注的补充附件，其既有子附件迁移至主条目。原PDF、任务、产物、IR、摘要、证据和外部来源路径保持独立身份；子附件回收标记保留。集合和标签取并集；所选主条目的不同笔记带来源ID合并，超出既有字段上限则拒绝，不截断。内部 mergeNoteSources 保存贡献列表；只有当前笔记仍逐字等于受控格式化结果才复用贡献，多轮合并不再次嵌套来源标题；人工修改后的笔记按新的真实原文保留，不猜测文本头部。

SQLite v10 升级前备份 library-before-v10.sqlite3。关系迁移、主书目更新、合并前书目/关系快照与固定 documents_merged 审计同事务，不删除文件。`list_merge_history(master_id)` 供本地查看历史，快照不作为自动撤销接口；合并不可自动撤销。显式永久删除所属文献才级联清除历史。外部已导出的引用不自动改写，不宣称 Zotero Word 插件等价。

## 无文件书目与主要 PDF（v11）

`create_bibliographic_item(metadata,collection_id=None)` 复用既有元数据校验，创建四类书目并可同事务加入集合；标题必填，审计失败全部回滚。无文件条目 `fileKind='bibliographic'`，SHA/filename/sizeBytes/primaryPdfId 为 null，sourcePaths 为空；数据库 SHA 为真正 NULL，没有占位文件。`hasPdf` 表示该身份本身是真 PDF，根条目的 `primaryPdfId/hasAnyPdf` 表示当前可用的主要文件。

`primary_pdf_id(root_id)` 返回活动、真实、归属正确的 PDF ID；旧 PDF 根默认使用自身。`set_primary_pdf(root_id,pdf_id)` 显式选择并与固定审计同事务。首个后加 PDF 在尚无保存选择时自动设为主要文件；主要附件移入回收站后返回 None，保存原选择供恢复，不回退到其他附件。已有选择被隐藏时新增附件不替换；恢复不覆盖用户后来选择。永久删除主要附件同事务清空指针，仍不回退；以后新导入可成为主要文件。

无文件条目可组织、检索、导出引用、合并和回收，但读 PDF、IR、解析、全文索引、证据、翻译和模型摘要拒绝该身份，调用者必须选择实际文件 ID。`list_attachments` 只返回真实 PDF；内部文献列表包含所有书目/文件用于历史与保护，路径和哈希保护只取真实文件。无文件条目不显示为等待解析的全文覆盖项。

合并无文件来源保留 `merged_record` 关系，移走其真实子附件，不伪装成文件。空书目主项可继承来源已选择的活动 PDF；已有隐藏选择保持。预览返回计划 primaryPdfId，实际提交仍核对 revision；counts.records 是记录数，pdfs/attachments 只计真实文件。历史读取先检查家族快照总字节不超过32 MiB。

v11 升级前备份 library-before-v11.sqlite3；原子重建可空 SHA 表与扩展关系角色，保留旧 ID、哈希、索引、触发器、IR、成员与任务。重建期间暂关外键避免旧关系级联删除，提交前 foreign_key_check，所有常规连接开启外键。模式错误或关系损坏保持旧版本与数据，拒绝启动，不绕过校验。

## 本地引文导入

`preview_metadata_import(path,format)` 显式接受 bibtex/ris/csl-json，读取最多8 MiB UTF-8文件（可有BOM），完整解析最多1000记录，超限拒绝且不截断。BibTeX使用固定bibtexparser，RIS使用固定rispy，CSL采用严格JSON；不存在的文件、格式错误、重复JSON/BibTeX字段、非有限数字和未结束RIS记录均不静默忽略。

返回 token/format/sourceName/fingerprint/total/validCount/invalidCount/warnings/items。每项 index/sourceId/title/valid/metadata/warnings/errors；元数据复用既有四类型、日期、作者和字段校验。未知类型不可选；未导入来源字段、完整姓名的个人/机构歧义及未解释LaTeX命令明确警告，无法映射日期或冲突别名无效。URL、附件字段不访问，来源ID不用作本地ID。作者顺序保留，不猜拆名字；当前BibTeX学位论文导出用@misc及显式polyscholaritemtype标记，不推断博士。其他软件可忽略该自定义标记，不能宣称所有字段无损往返。

解析与完整IPC接收在独立spawn进程中受30秒预算监督，结果最多32 MiB；禁宏展开/交叉引用补填，不运行TeX。解析器原始诊断丢弃，不显示或写入原始来源日志。主桌面只保持最多8份、合计32 MiB可信预览，超量淘汰旧项；关闭清空并终止回收解析进程。冻结入口使用freeze_support，不在解析子进程创建Qt窗口或用户库；真实冻结包验收仍单列待执行。

`import_metadata_preview(preview,selected_indices,collection_id=None)` 只接受非空、唯一、真正整数的有效索引；拒绝篡改、失效或已消费token。源文件重新读取、哈希及解析结果核对后再提交，漂移必须重新预览。所选记录用新UUID在单事务内创建无文件书目、目标集合关系及固定citation_imported事件；失败无半批记录且token可重试，成功消费token。返回 documentIds/importedCount/collectionId；不自动去重合并，不改已有记录，不引入文件对象或下载任务。UI共享ManagedIODialog，显式勾选、不默认全选；失败保留草稿但解析失败或来源变化禁止沿用旧预览提交。


## 本机 Zotero 迁移与档案（v12）

`preview_zotero_migration(directory,linked_directory=None)` 只接受显式本地目录；可选 linked_directory 限定链接附件读取范围，相对 attachments: 路径和本平台范围内绝对路径可用，其他平台绝对路径不猜测。SnapshotReader用普通文件只读、禁止链接/非阻塞句柄复制并复核DB、WAL、SHM、journal；SQLite只操作受管副本。userdata白名单121/123/130并验证命名列与关系。返回token/sourceName/fingerprint/schemaVersion、items、collections、resources、counts与warnings。每item有sourceId/key/itemType/title/status/native或archive、metadata/deleted/warnings/selectable；子附件/笔记/批注由所选父条目传递纳入，不作为顶层重复选择。

`import_zotero_preview(preview,selected_ids=None)` 验证完整可信预览、唯一顶层字符串ID及来源/真实资源未漂移。UI显式非空勾选；服务None表示所有可选顶层条目。部分选择只归档所选传递子图与相关集合、字段值、作者、标签、library/group；全选保留已支持读取的全图谱及空集合/保存搜索。源类型、字段、HTML笔记、批注位置或未复制资源不丢弃为假成功，原始档案与原生四类型适配分别计数。

返回持久receipt：id/sourceName/sourceDirectory/linkedDirectory/schemaVersion/fingerprint/createdAt/selectedSourceIds/counts/mappings/sourceIdentities/collectionMappings/warnings。sourceIdentities保存libraryID/key/localId；本地UUID独立于来源。nativeActive/nativeTrashed、pdfs、archivedResources与archive分别计数。真实受管PDF每来源附件独立ID，允许多个父条目共用哈希对象；普通无所属参数的重复PDF导入遇多owner时拒绝歧义。受管其他/独立文件真实字节归受管档案对象；链接附件、受管HTML伴随文件、嵌入图像及按库类型定位的批注缓存保存真实字节。缓存未解码、原生批注未恢复；链接HTML周边未映射仍明示pending。

`list_zotero_migrations()` 返回收据列表；`read_zotero_migration(id)` 返回{receipt,archive:{tables,items,resources}}，原始HTML只供纯文本查看。`export_zotero_resource(receipt_id,source_id,destination)` 的第二参数传资源 resourceId（旧单文件资源仍等于 sourceId）。此接口 仅导出已保存档案字节，校验固定receipt/hash路径、普通单链接、尺寸/身份/哈希后共享protected atomic_export；不自动执行文件。普通原件与整个来源Zotero目录与已选链接目录纳入持久防覆盖保护。

`cancel_zotero_migration()` 在复制、SQL检查、分块发布及提交前触发检查；提交后是真实成功收据，不假报回滚。PDF元信息通过spawn固定小结果监督，不在Qt进程调用原生解析；10秒或总预算剩余时间，失败/超时只保字节到档案。关闭终止回收活动校验进程并清临时副本。SQL迁移v12先备份，原生条目、集合、来源映射、档案和zotero_migrated审计同事务；失败清本轮新资源，不删除旧共享文件。持久发布日志登记文件身份后用硬链接发布；提交后清暂存，返回publicationCleanupComplete。启动在库锁和模式成功后恢复合法日志，身份替换/未知文件保留pending；详见迁移范围。进程崩溃回归不替代断电、Windows ACL或凭据清理验收。

导入导出原生中心只路由已有PDF、引文、Zotero、译文和档案操作，复用校验、worker生命周期及导出保护。源码回归不替代真实Zotero含条目库或冻结安装包验收。

资源报告新增resourceId；主文件仍取sourceId，快照子文件取sourceId:snapshot:relativePath，批注缓存取sourceId:annotation-cache。sourceId用于所选条目传递子图，resourceId用于逐文件归档及导出，避免同一源条目的多个文件覆盖。
