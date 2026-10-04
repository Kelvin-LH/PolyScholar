# PolyScholar 命令行说明(cli.md)

本文件是 PolyScholar 的**命令行工具契约**,供 AI agent(经 MCP 或直接 shell)程序化操作软件。所有命令直接调用与图形界面相同的本地服务层:同样的校验、审计与存储;界面与命令行修改同一份数据。

安装后命令名为 `polyscholar-cli`(开发环境可用 `python -m polyscholar.cli`)。所有命令支持 `--json` 输出机器可读结果;**给 agent 的建议:始终加 `--json`**。

## 全局约束(agent 必读)

- **单实例**:GUI 与命令行不能同时打开同一资料目录。请先让用户关闭 GUI,或先用 `status` 探测锁状态。冲突时报错"此文献库已在运行"。
- **退出码**:0 成功;1 失败(错误写入 stderr,已脱敏,不含密钥与论文正文);2 库被其他进程占用;3 网络失败(如 arXiv 不可达);130 中断。
- **文献引用 `<doc>`**:接受完整 id、id 前 8+ 位前缀、或唯一标题片段。不唯一时会列出候选。
- **密钥**:翻译在调用 LLM 时使用设置页保存的密钥(配置文件或系统凭据库)。密钥不通过命令行传递。
- **删除保护**:进行中的任务阻止删除文献;原始导入文件永不被删。
- **网络与代理**:`add --arxiv` 遵循标准 `HTTPS_PROXY`/`HTTP_PROXY` 环境变量。通过 MCP 使用时,把代理写进 MCP 配置里 polyscholar 服务器的 `env` 后重启客户端即可生效。

## 命令一览

### 文献

```
polyscholar-cli add <pdf路径> [--meta meta.json] [--quiet]
                                                 # 导入本地 PDF;--meta 应用显式元数据(JSON 文件)
polyscholar-cli add --arxiv <链接或编号[,编号2,…]> [--collection 集合名] [--quiet]
                                                 # 拉取 arXiv 元数据+下载正文+自动填写;支持一篇或一批
polyscholar-cli list [--json] [--quiet]          # 列出文献;--quiet 时 --json 仅含 id/标题/年份/评分
polyscholar-cli show <doc> [--text] [--pages 2-5] [--json]
                                                 # 详情;--text 附全文块,--pages 只取页范围(省 token)
polyscholar-cli search <关键词> [--doc <doc>] [--limit N] [--json]
                                                 # 本地全文检索:默认全库,--doc 限定单篇(含附件);定位原文用,免整篇重读
polyscholar-cli update <doc> [--title T] [--authors A] [--doi D] [--year Y]
                        [--url U] [--abstract X] [--tags 标签1,标签2]
polyscholar-cli remove <doc> [--yes]             # 删除(--yes 供 agent 跳过确认)
polyscholar-cli text <doc> [--json]              # 输出解析后的全文块(页码+块id+文本)
polyscholar-cli parse <doc>                      # 解析 PDF 生成文本块(翻译/检索的前置)
polyscholar-cli status [--json]                  # 库状态:锁是否被 GUI 占用、文献/评分/任务计数
```

- 本地 PDF 没有书目信息,`add <pdf>` 只以文件名为标题。需要元数据时用 `--meta`(JSON 对象,允许字段:title/authors/doi/year/url/abstract/tags/itemType/creators),导入后立即应用并走与 `update` 相同的校验;程序不会从 PDF 猜测元数据。`--meta` 仅支持单篇。
- `add --arxiv` 自动填写标题、作者、日期、摘要、DOI、URL,并设条目类型为 arXiv 预印本;多篇批量时输出 `{imported:[{id,title}],errors}`,单篇(非 --quiet)仍输出完整文献。
- agent 阅读论文:`show <doc> --text --json` 全文,或 `--pages 2-5` 取页范围;复核定位用 `search <关键词> --doc <doc> --json`(需先 parse;返回页码+块id+摘要,不整篇重读)。

### 集合与标签

```
polyscholar-cli collection add <名称> [--parent 父集合]
polyscholar-cli collection remove <集合引用>
polyscholar-cli collection list [--json]
polyscholar-cli collection attach <doc> --collection <集合引用>
polyscholar-cli collection detach <doc> --collection <集合引用>
```

标签属于文献字段:用 `update --tags a,b,c` 整体设置。

### 翻译

```
polyscholar-cli translate <doc>          # 默认使用设置中的 PDF 引擎，前台等待
polyscholar-cli translate <doc> --engine html-llm  # arXiv 全文英文转中文 HTML
polyscholar-cli translate <doc> --engine babeldoc
polyscholar-cli jobs [--json]            # 任务列表
polyscholar-cli export-translation <doc> [-o 输出.html]
```

- PDF 路线保留 BabelDOC/PDFMathTranslate；HTML 路线要求 arXiv 链接，按全文处理。
- CLI 不支持脱离托管的后台翻译；`--no-wait` 在创建任务前拒绝。
- 产物 `translated.html`:纯中文、保留图表与公式;通过受管工作进程生成，未翻译段落标记保留原文。
- 译文归属于文献:同一文献的多次翻译,最新版在"打开译文"中生效。

### 提炼(AI 提炼维度:两个子代理)

```
polyscholar-cli score aggregate --kind summary --reports A.json B.json     [--apply <doc>] [--out summary.json] [--rationale 一句话概览] [--json]
polyscholar-cli score set <doc> --kind summary     --rationale "一句话概览" --detail-file summary.json   # 手工写入仍可用
```

- **提炼报告契约**(每个隔离子代理各写一份,JSON 对象,仅允许四个字段):
  `{"problem":[{"text":"…","evidence":"§1 可选出处"}],"method":[…],"results":[…],"limitations":[…]}`
  分别对应**解决的问题 / 使用的方法 / 实验效果 / 不足与缺陷**;每节 1–16 条,text ≤2000 字节,evidence ≤500 字节可选。
- **合并是程序化的**:`score aggregate --kind summary` 恰好接收两份报告,校验契约后按 A→B 逐节合并,
  规范空白后完全相同(忽略大小写)的条目合并为一条并保留双方代理标记,措辞不同的一律保留,无模型裁量;
  `--apply <doc>` 直写提炼维度(分数为空,理由缺省自动生成条数概览),`--out` 同时落盘。
- 写入后 GUI 文献详情"AI 提炼"框显示理由,"查看完整评分明细"的 AI 提炼页显示四节清单及各条来源代理。
- `score validate` 不支持 summary(提炼没有分数聚合);报告校验由 aggregate 合并前自动完成。

### 评分(paper / confidence)

```
polyscholar-cli rubric list [--json]             # 两份打分文档的路径/版本/SHA-256/大小
polyscholar-cli rubric show paper [--json]       # 论文评分 rubric 全文(冻结材料包用)
polyscholar-cli rubric show confidence [--json]  # 置信度 rubric 全文
polyscholar-cli score aggregate --kind paper \
    --reports A.json B.json C.json \
    [--recheck RA.json RB.json RC.json] \
    [--out report.json] [--apply <doc> --rationale-file r.md] [--json]
polyscholar-cli score validate --kind paper --detail-file report.json [--json]
polyscholar-cli score import-report <doc> --kind paper --agent A --detail-file A.json
polyscholar-cli score reports <doc> --kind paper [--agent A] [--json]
polyscholar-cli score set <doc> --kind paper --score 87.5 \
    --rationale-file r.md --detail-file report.json
polyscholar-cli score show <doc> [--summary] [--json]
```

- **先取 rubric**:用 `rubric show paper|confidence` 直接读取打分文档全文,不必在文件系统里找路径;返回值含版本号与 SHA-256,可直接写进冻结材料包 manifest。**评分必须以 rubric 为唯一依据**,禁止引入 rubric 之外的标准。
- **三盲评合卷已程序化**:`score aggregate --reports A.json B.json C.json` 依次完成——逐份结构校验(rubric_version/manifest/维度和=total/刻度)、核对三份版本与冻结材料包一致、取中位数、按 A→B→C 平局规则选中位数那位评委的代表报告、嵌入三份首轮原文(sub_scores[].original_output)、生成 rubric §6 汇总报告。agent 只需让三个隔离子代理各写一份报告文件,再调一次 aggregate,**不需要自写聚合/校验脚本**。
  - 首轮极差超过 10(论文)/15(置信度)时,aggregate 不合卷,返回 `needs_recheck:true` + 分歧条目清单 + 已填好的复核指令模板;三个子代理复核后用 `--recheck` 重新聚合(复核原文存入 recheck_output,rounds=2)。复核后仍超限则按 rubric 输出中位数并标 `unresolved_disagreement:true`。
  - `--apply <doc>` 把聚合结果直接写入文献评分(score=中位数,--rationale/--rationale-file 为理由),`--out` 同时落盘 JSON——两步合成一步,agent 无需把报告内容读进上下文。
- `score validate` 对任意汇总报告做同样的结构校验(含 median/spread/复核规则),失败逐条列出,退出码 1;`score set` 前建议先 validate。
- `score import-report` 把三位评委的原始报告按 (文献,类型,评委槽位) 归档进库(≤1 MiB/份,重复导入覆盖同槽位);`score reports` 列出归档清单(--agent 时原样读回全文)。评分写库与原文归档相互独立,建议都做。
- `score show --summary` 剔除内嵌评委原文,只返回分数/维度/聚合骨架——读状态用 summary,核对原文用 `reports --agent`。
- rubric 规定三子代理盲评取中位数:三个隔离会话由 agent 侧组织(rubric §4.1),聚合、校验、复核判定全部由 aggregate 程序完成;`--score` 填最终中位数(用 --apply 时自动)。
- `aggregation.rounds` 语义按 rubric §6.1:**未复核=0,复核过=1**(不是合卷次数);validate 会强制校验。
- `score set`/`aggregate --apply` 成功回执只含 `{id,kind,score,detail_bytes}`,不回显明细 JSON——写入的内容调用方本来就有,需要时用 `score show` 读回,避免单次几十 KB 的重复输出。

### 文本提取(agent 阅读论文)

```
polyscholar-cli parse <doc>
polyscholar-cli text <doc> --json        # [{pageNumber,id,text},...]
```

## 典型 agent 工作流

**评分一篇论文(paper)——全程程序化,agent 只写三份报告文件**

```sh
polyscholar-cli rubric show paper > /tmp/rubric.md       # 取打分文档全文+版本+SHA-256
polyscholar-cli parse <doc>
polyscholar-cli text <doc> > /tmp/paper.txt              # 或 search/--pages 取需要的部分
# 三个隔离子代理各输出 /tmp/{A,B,C}.json(完整 agent_report);agent 不读其内容
polyscholar-cli score aggregate --kind paper \
    --reports /tmp/A.json /tmp/B.json /tmp/C.json \
    --apply <doc> --rationale-file /tmp/rationale.md
# 极差超限时 aggregate 返回复核指令:子代理复核后加 --recheck RA.json RB.json RC.json 重跑
polyscholar-cli score import-report <doc> --kind paper --agent A --detail-file /tmp/A.json   # B/C 同理
```

**提炼一篇论文(summary)**

```sh
polyscholar-cli parse <doc>
polyscholar-cli text <doc>
# 生成 {"problem":...,"method":...,"domains":[...],"findings":[...]}
polyscholar-cli score set <doc> --kind summary \
    --rationale "一句话概览" --detail-file /tmp/summary.json
```

**批量入库 + 入集合**

```sh
polyscholar-cli collection add 自动驾驶
polyscholar-cli add --arxiv https://arxiv.org/abs/2503.19755 --collection 自动驾驶
polyscholar-cli translate <doc>
```

## 明确不做

- 不做学术搜索、不下载非 arXiv 来源、不绕过权限访问论文。
- 不提供"自动判断科学真伪"的命令;置信度评分只是文本内证据的量化。
- 不收集遥测;所有操作记录在本机审计日志。

## 版本与状态

- CLI v1.5(2026-10-03 第五轮):修复老库 summary 写库回归(数据库 v12 重建 desktop_scores,升级前备份;旧库 CHECK 约束缺 summary 类型导致提炼写不进去);`aggregation.rounds` 修正为 rubric 语义(0=未复核/1=复核过)并纳入 validate;`score set` 回执精简为 {id,kind,score,detail_bytes};数据库约束错误转为可读提示(原为 traceback)。
- CLI v1.4(2026-10-03 第四轮):`score aggregate --kind summary` 两子代理提炼合卷(问题/方法/效果/不足四节契约,程序合并去重,--apply 直写);评分明细对话框改为按类型选项卡,置信度/提炼一键直达。
- CLI v1.3(2026-10-03 第三轮):移除应用内证据摘要(summarize 命令与 GUI 页面;数据库 v11 备份后删除 claims 表),要点分析由外部 agent 完成后经 `score set --kind summary` 写入。
- CLI v1.2(2026-10-03 第二轮):新增 `score aggregate`(三盲评合卷:校验/中位数/代表报告/复核指令/`--apply` 直写)、`score import-report`/`reports`(评委原文归档与读回,数据库 v10)、`search`(本地全文检索,支持 --doc 限定单篇)、`show --pages`/`score show --summary`;`add --arxiv` 支持批量。目标:能程序化的都在程序内完成,agent 不再自写聚合/校验/grep 脚本。
- CLI v1.1(2026-10-03):新增 `rubric`/`status`/`score validate`、`--meta`/`--quiet`/`--rationale-file`,退出码 2(库占用)/3(网络失败);修复 collection attach/detach 契约与实现不一致。GUI 文献详情新增"查看完整评分明细"(只读渲染 detail JSON,可导出)。
- CLI v1(2026-10-02):覆盖文献/集合/翻译/评分读写。MCP 服务将把上述命令逐一映射为 MCP 工具,以本文件为契约文档。


## MCP 接入

本文件同时是 MCP server 的工具契约:`polyscholar-mcp`(stdio)只暴露两个工具——`cli_docs`(返回本文档全文)与 `cli_run(command)`(白名单执行上述命令)。安装与客户端配置见 [mcp.md](mcp.md)。agent 的推荐用法:`cli_docs` 学习命令 → `cli_run` 执行(输出加 --json)。

评分为 Agent 评阅记录，不自动判定科学结论真伪。默认资料目录与桌面一致；不会启动时自动复制其他资料库。
