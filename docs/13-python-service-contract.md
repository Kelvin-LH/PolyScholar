# Python 原生桌面服务合同

用户选择全 Python 实现后，本合同替代旧 Rust/Tauri IPC 合同。PySide6 直接调用 `polyscholar.service.LocalService`，没有浏览器、WebView、HTTP 服务或团队账户。数据与结果保存本机。引擎运行时使用随应用提供的两份独立 CPython，用户无需安装或配置 Python。

## 调用接口

`LocalService(data_dir=None, resources_dir=None)`。默认资料目录：冻结发行采用各平台标准应用数据目录；源码运行为仓库内 `data/`（便携，.gitignore 排除），首次启动自动从旧 AppData 位置完整复制文献库/原件/缓存，旧目录保留为备份；打包资源位于 macOS `Contents/Resources/resources`，Windows/Linux `_MEIPASS/resources`。开发时使用工程资源根目录。

- `list_documents() -> list[dict]`
- `import_pdf(path) -> dict`：100 MiB 限制、PDF 文件头验证、SHA-256 去重、只读本地对象副本。
- `update_document(document_id, patch) -> dict`：标题、作者、DOI、年份、标签、摘要、来源链接和本地笔记。
- `delete_document(document_id)`：保护正在执行的任务；删除库中副本与该文献任务产物，保留外部原文件。
- `read_pdf(document_id) -> bytes`：供 QtPdf 的 QBuffer 使用。
- `get_settings() -> dict`；`save_settings(settings) -> dict`。
- `set_session_key(key)`：密钥仅存进程内存，传空文本清除，关闭应用清除；不写 JSON、SQLite、日志。
- `store_api_key(key, mode='os')`：校验后激活会话密钥并按所选方式持久化。`mode='os'` 写入 OS 凭据库（Windows 凭据管理器/macOS 钥匙串/Linux Secret Service，经 keyring 25.6.0）；`mode='file'` 按用户 2026-10-02 的显式选择写入明文配置文件 `~/.polyscholar/api_key`（与常见 CLI agent 的 auth.json/token 文件一致，POSIX 0600，Windows 依赖用户主目录默认 ACL）。`clear_stored_api_key()` 同时删除两种存储；`stored_key_location()` 报告当前存储位置（'os'/'file'/None）。`start_translation`/`list_models` 未传会话密钥时按"会话 → 凭据库 → 文件"回退。无安全凭据库时 OS 方式明确报错；明文文件仅由用户显式选择产生，文件纳入备份同步或被同用户程序读取的风险在设置页与文档说明。
- `discover_engine(engine) -> {pythonPath, available, message}`：只查应用 `runtime/<engine>/bin/python3` 或开发 `.runtime/<engine>`，Windows 查 `python.exe`/`Scripts/python.exe`；独立验证 BabelDOC 0.6.4、PDFMathTranslate 1.9.11。不存在时明确报告，不回退到系统 Python。
- `list_models(endpoint=None, key=None) -> list[str]`：使用会话密钥 GET OpenAI 兼容 `<base>/models`，超时 15 秒、响应最多 1 MiB、禁止重定向凭据；去重排序，失败只显示脱敏提示，允许手动填写模型名。GUI 应在 QThread 调用。
- `arxiv_lookup(query, endpoint=None) -> list[dict]`：解析粘贴的 arXiv 链接或编号，向 arXiv Atom API 查询公开元数据；仅发送论文编号，详见文末 arXiv 导入一节。
- `arxiv_import(entries, collection_id=None)`：下载所选条目 PDF 并按既有哈希去重入库，自动填写书目字段；逐条返回成功与脱敏错误，详见文末 arXiv 导入一节。
- `list_jobs() -> list[dict]`
- `start_translation(document_id, pages='') -> dict`：2026-10-02 起 HTML 路线——解析条目的 arXiv 链接，后台线程执行 `html_translate.translate_paper`（arXiv/ar5iv HTML 逐段送用户配置的 LLM，并发 4，MathML 占位保留），产物为 `translated.html` 双语页；`pages` 参数保留但忽略（总是全文）。要求条目带 arXiv 链接；无链接明确拒绝。首版全局一个活动任务。旧 BabelDOC/pdf2zh 运行时路线已删除。
- `read_artifact_pdf(job_id, artifact_index=0) -> bytes`
- `export_translation(job_id, artifact_index, path)`：仅可复制已完成任务的真实 PDF；拒绝产物路径穿越、内部译文覆写、源文献对象库覆写及硬链接别名；目标由原生保存对话框选择。
- `export_metadata(document_ids, format, path)`：`document_ids` 可为单个 ID 或 ID 列表；格式为 `csl-json`、`bibtex`、`ris`。属于元数据交换，不承诺 GB/APA 等最终排版。
- `job_output_dir(job_id) -> Path`：已完成任务的真实产物目录（仅限应用自建 UUID 目录），供"打开目录"使用。
- `attachment_file_path(document_id) -> Path`：库内 PDF 的本机对象路径，供附件双击以系统默认程序打开。
- 任务成功后，单译文产物（优先 mono，否则 dual）自动挂载为所属主文献的"已有译文"附件；子附件的译文挂载到其主文献。挂载失败仅忽略，不影响任务完成状态。
- `close()`：清除密钥、终止活动 worker 并等待回收；远程已发送请求可能计费。GUI 退出时调用。

文献字典字段：`id,title,authors,doi,year,tags,notes,abstract,url,sha256,filename,sizeBytes,createdAt`。作者使用分号分隔，`tags` 为字符串列表，时间为 UTC ISO8601。

设置字段：`endpoint,model,engine,pythonPath,cachePath,sourceLanguage,targetLanguage,doiEnabled,timeoutSeconds,keyStorage`（`keyStorage` 记住用户选择的密钥存储方式：空/os/file，本身不是密钥）。`pythonPath` 只用于兼容旧设置/运行状态，实际引擎路径受应用控制，无用户安装要求。模型名默认为空，通过服务查询或手动填写。拒绝设置中出现额外字段或 API 密钥；endpoint 拒绝凭据、query、fragment，要求 HTTPS 或本机环回 HTTP。

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


## 类型化文献信息（保持 v6）

`update_document` 支持 `itemType`：arxiv-preprint（2026-10-02 新增，用户明确本程序以 arXiv 为主）、article-journal、paper-conference、book、thesis。arXiv 预印本仅适用期刊/论文集与日期字段，年份由日期派生；CSL 导出为通用 article，BibTeX 用 @misc，RIS 用 GEN，不冒充正式出版物。`creators` 为有序列表，含 role=author/editor、type=person/organization；个人使用 literal 或 family/given，机构仅 literal。旧作者文本只按分号分隔，保留完整姓名，不推断姓与名。普通字段编辑保留作者身份与顺序。

出版字段包含 publicationTitle、publisher、place、date、volume、issue、pages、isbn、edition、eventTitle、institution、thesisType。日期采用 ASCII YYYY / YYYY-MM / YYYY-MM-DD 并校验日历；日期与年份冲突在写入前拒绝。切换类型保留暂不适用字段，界面提示，导出仅包含当前类型适用字段。现有 v6 JSON 扩展不需要 SQL 迁移或重写文件 ID。

CSL JSON 保留类型、作者/编者角色、日期和出版字段；BibTeX 普通学位论文使用 @misc 加 type，避免推断为博士；RIS 保留 AU/A2 与类型、出版字段。导出清理控制字符及 Unicode 换行符，BibTeX 转义特殊字符；预览与保存一致。子 PDF 附件不能独立导出书目引用。RIS 不具有与本地结构化作者相同的身份语义，尚不承诺外部导入无损往返或 CSL 样式排版。字段参考 [CSL schema](https://github.com/citation-style-language/schema/blob/master/schemas/input/csl-data.json) 和 [Zotero BibTeX translator](https://github.com/zotero/translators/blob/master/BibTeX.js)。


## 高级元数据检索与保存搜索（v7）

`search_documents` 新增可选 `query={match:'all'|'any',conditions:[{field,operator,value}]}`，与既有快速搜索、集合、未分类和标签筛选取交集。字段为 title/creator/doi/year/itemType/publicationTitle/publisher/isbn/tag/notes；creator 包括个人、机构及编者的完整姓名或姓/名。文字支持 contains/not_contains/is/is_not/is_empty/is_not_empty，year 额外 before/after。Unicode casefold 比较，百分号及下划线按普通文字，未使用 SQL 通配符。多人/多标签负条件要求所有值均不匹配，空字段满足负条件；未知年份不参与数值比较。

规则限制 1–20 条，每值最多 4 KiB；未知字段/操作、空条件和值、非 ASCII 数字的年份比较在执行前拒绝。类型转换保留的元数据也可被字段条件检索。搜索不读 PDF 正文，不联网，也不运行表达式代码。

`list_saved_searches/save_saved_search(name,query,search_id=None)/delete_saved_search` 在本地保存命名规则，返回 id/name/query/createdAt/updatedAt。保存的是规则，不是结果 ID，也不自动保存当前集合/快速搜索范围；打开时动态计算并叠加界面当前筛选。规则编辑须主动更新保存项，取消不写库。SQLite v7 升级前备份 `library-before-v7.sqlite3`，新增保存搜索表，旧文献、附件、集合和证据保持。迁移的新表、审计触发器及版本号同事务；保存搜索新增/更新/删除审计与数据同事务，仅固定事件和时间。

此增量对标 [Zotero 官方高级搜索与保存搜索](https://www.zotero.org/support/searching)，尚未包含全文索引、嵌套保存规则或日期相对条件。


## AI 评分维度（v9）

用户决策：2026-10-02 起软件转型为 AI 使用的文献保存库。新增 `desktop_scores` 表（v9，升级前备份 library-before-v9.sqlite3）：document_id+kind 主键。kind=paper 论文评分 / confidence 置信度评分（score 0–100 可空，rationale 得分/失分理由，detail 为评分明细 JSON：维度分、三子代理子评分、红旗等）；kind=summary AI 提炼（2026-10-02 新增：score 恒空，rationale 为一句话概览，detail 为结构化 JSON——problem/method/domains/findings 等），供 agent 快速了解论文解决的问题、方法、适用领域与发现的问题。

`set_scores(document_id, entries)` 校验并 upsert（每文献每类一条，重评覆盖并记审计 score_updated）；`document_scores(id)` 返回两类评分；`list_documents/list_root_documents` 附带 `scores:{paper,confidence}` 数值供列表排序。评分的**执行**由外部 agent 依据打分文档完成；两份打分文档（2026-10-02 由用户用 prompts/ 提示词经 GPT 生成）已就位于 `rubrics/paper-scoring.md`（v1.0.0，五维 20 条目连续条件计分）与 `rubrics/confidence-scoring.md`（v1.0.0，四维 20 条目+红旗扣分+中心结论上限），均含三子代理盲评中位数汇总与机器可读 JSON Schema（agent_report/汇总报告两级），MCP/命令行接口后续交付；本程序只负责存储、校验、展示与排序。

界面调整：文献列表改为三列（标题/论文分/置信度）可点击表头排序，未评分排在最后；笔记功能自界面移除（历史 JSON 数据保留但不再展示与写入，存储字段仍兼容）。

## 本地 PDF 全文索引（v8）

`search_fulltext(text,collection_id=None,unfiled=False,tags=None,include_descendants=False,query=None,limit=200,metadata_text='')` 检索当前 DocumentIR 文本。元数据规则先筛主条目，再检索其家族 PDF；命中保留实际 documentId/parentDocumentId/revisionId/blockId、页码及坐标，子附件不冒充主 PDF。短字及中文采用字面子串；Python casefold 后三字符以上走 SQLite FTS5 trigram 候选与 instr 精确校验，一、二字符走索引文本扫描。百分号、引号、OR 等不是表达式。返回精确 total、显式 truncated，默认最多200条、允许1–1000；每项只返回最多512字符原文摘录，不返回完整大块文本。全文 SQL 和摘录计算检查30秒预算，超时拒绝部分结果；metadata 筛选沿用现有实现，并在进入/返回全文阶段检查预算。

coverage 区分 indexed/no_text/cleared/unparsed/parse_failed；最近重解析失败且仍有有效旧版本时保留索引，并显示 lastParseStatus=failed、previousCurrent 和固定错误码，不伪称重新解析成功。空查询仅返回覆盖状态。定位前核对当前解析版本及文本块身份。

SQLite v8 升级前备份 library-before-v8.sqlite3，回填已有当前版本；索引替换与解析版本写入同事务，删除 PDF 级联删除索引。`clear_fulltext_index/rebuild_fulltext_index(document_id=None)` 只清除/重建索引，保留 IR、原文及证据；重开不会自动恢复已主动清除的索引。重建索引不掩盖最近解析失败。解析和搜索由原生桌面受管理的工作线程执行，关闭窗口等待操作结束。

搜索完全本地，不执行 OCR 或模型调用，不处理图片中的不可提取文字。实现依据 [SQLite FTS5 trigram 文档](https://www.sqlite.org/fts5.html#the_trigram_tokenizer)。


## MCP server（mcp.md）

`polyscholar/mcp_server.py`(mcp 2.3.0,可选依赖组 `mcp`)以 stdio 运行,只暴露 `cli_docs` 与 `cli_run` 两个工具:前者返回 cli.md 契约全文,后者白名单执行 CLI 子命令并回传退出码与输出(上限 100 KiB)。server 不含业务逻辑;软件用途在 server instructions。`POLYSCHOLAR_DATA_DIR` 指定资料目录。配置见 mcp.md。

## 命令行入口（cli.md）

`polyscholar/cli.py` 与 GUI 共用 LocalService 契约：add/list/show/update/remove/text/parse/collection/translate/jobs/export-translation/summarize/score。`--json` 输出机器可读结果；文献引用支持 id/id 前缀/唯一标题片段；错误脱敏写 stderr、退出码 1。单实例锁与 GUI 互斥(先关闭 GUI)。`score set` 是 agent 打分的写入端：rubric 阅读与三子代理盲评由 agent 侧执行，程序只校验并存储。面向 agent 的完整命令说明见仓库根 cli.md。

## arXiv 导入（保持 v8）

`arxiv_lookup(query, endpoint=None)` 解析粘贴的 arXiv 链接或编号（每行一个、去重、上限 50；新式 `2312.04567` 与旧式 `cs/0301012` 均可，裸旧式编号仅接受真实档案前缀），向 arXiv Atom API 查询标题、作者、日期、摘要、DOI 与 PDF 链接。请求只发送论文编号，带固定 User-Agent；元数据响应上限 4 MiB、超时 30 秒；地址要求 HTTPS 或本机环回 HTTP。

`arxiv_import(entries, collection_id=None)` 逐条下载所选 PDF（校验文件头与 100 MiB 上限，条目之间保留间隔遵守上游礼貌策略），按既有 SHA-256 去重入库，自动填写标题、作者、日期、摘要、DOI 与来源 URL，可加入指定集合。作者存为结构化完整姓名（literal），不推断姓与名。逐条返回成功与脱敏错误，单条失败不影响其余条目；条目数据在入库前按白名单键与长度校验。

文献字典新增 `abstract` 与 `url` 字段（JSON 扩展，无 SQL 迁移，旧条目读取时补空值）；CSL JSON/BibTeX/RIS 导出包含摘要与链接，RIS 摘要按既有规则清理换行。真实 arXiv 网络载荷、限流与重定向行为未验证；环回合成测试见 `tests/test_arxiv.py`。
