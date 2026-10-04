# MCP 接入

PolyScholar 通过 stdio 让 AI 客户端使用本机文献库。桌面与 MCP 复用同一 CLI、校验、事务及库锁，不引入 HTTP 服务、账户或云同步。

## 工具与资源

| 入口 | 用途 |
|---|---|
| `library_status` | 查看配置库的占用状态、只读模式和下一步操作 |
| `cli_execute(argv)` | 推荐入口：使用参数数组执行命令，支持空格、中文和 Windows 路径 |
| `cli_docs` | 读取完整命令契约 |
| `cli_run(command)` | 兼容旧客户端，按 POSIX 引号规则拆分字符串 |
| `polyscholar://docs/cli` | Markdown 命令资源 |
| `polyscholar://rubrics/paper` | 论文评阅规则 |
| `polyscholar://rubrics/confidence` | 证据可靠性评阅规则 |

先调用 `library_status`，再读取命令契约。例如：

```json
{"argv": ["add", "D:\\研究资料\\paper one.pdf", "--json"]}
```

```json
{"argv": ["show", "文献 ID", "--text", "--pages", "1-3", "--json"]}
```

`cli_execute` 返回稳定字段：

```json
{
  "success": true,
  "error_code": null,
  "exit_code": 0,
  "stdout": "[]\n",
  "stderr": "",
  "truncated": false,
  "operation": "read"
}
```

完整有效的 JSON 输出还会解析为 `data`，截断时不提供此字段。

`operation` 为 `read`、`write` 或 `network`；联网命令也可能写入本地库。常见错误包括 `library_busy`、`read_only`、`library_override`、`timeout`、`command_failed` 和 `unsupported_runtime`。工具使用参数列表启动进程，无 shell；限制参数数量和长度、30 分钟总超时，以及每个输出流 100 KiB 的内存。截断时请缩小查询范围。

## 安装与配置

源码或独立 Python 环境安装：

```sh
python -m pip install -e ".[mcp]"
```

MCP 客户端配置：

```json
{
  "mcpServers": {
    "polyscholar": {
      "command": "polyscholar-mcp",
      "env": {
        "POLYSCHOLAR_DATA_DIR": "D:\\研究资料\\PolyScholar"
      }
    }
  }
}
```

省略 `POLYSCHOLAR_DATA_DIR` 时使用桌面的系统应用资料目录。库目录在连接启动时固定，工具不能通过 `--data-dir` 或其缩写切换文献库。

只读接入可增加：

```json
{"POLYSCHOLAR_MCP_READ_ONLY": "1"}
```

只读模式限制命令策略，允许检索、查看、读取规则及验证评阅报告；拒绝导入、解析、修改、导出、评阅写入、聚合和联网翻译。默认保留写入能力。CLI 启动仍可能初始化或迁移所选本地库；该模式不等于 SQLite 只读连接或操作系统文件沙箱。因此通用执行工具仍标注可能有本地副作用。

`POLYSCHOLAR_CLI_MD` 和 `POLYSCHOLAR_RUBRICS_DIR` 可指定公开契约文件位置。文档默认从源码、wheel 安装资源或桌面资源定位。arXiv 请求需要代理时，可设置 `HTTPS_PROXY`、`HTTP_PROXY`。

## 使用范围

连接 stdio MCP 即允许客户端按命令能力访问所选本地库。普通模式可以读取文献正文、修改库、导入所选文件及导出到所选路径；命令白名单不是文件系统沙箱。客户端的工具确认与用户授权仍需由客户端执行，软件不自动替用户批准操作。

GUI 占用同一库时，`library_status` 返回 `lock: busy`，其余库命令返回 `library_busy`；请先关闭使用该库的桌面应用。进程超时时终止整棵进程树；无法确认清理完成时返回 `cleanup_failed`，不把清理失败误报为成功。

联网仅涉及配置模型 API、DOI 和公开 arXiv 资源等已允许用途。翻译可能产生 API 费用；MCP 不提供密钥读出命令。评阅来自 Agent，程序负责结构校验、聚合与保存，不判断科学结论真伪。

桌面安装包的独立 stdio MCP 组件仍待验收；冻结桌面不能作为 `python -m` 命令宿主，当前明确返回 `unsupported_runtime`。源码与独立 CLI/MCP 的测试结果不等于桌面安装包验收。
