# MCP 接入指南

PolyScholar 提供一个 **stdio MCP server**,让 Claude Desktop、Cursor 或任何支持 MCP 的 AI 客户端直接操作你的文献库:导入/arXiv 拉取论文、集合管理、触发翻译、读取全文、写入 AI 评分与提炼(内置证据摘要页已移除,要点分析由 agent 完成后写入提炼维度)。

## 设计:只暴露两个工具

server 是 `polyscholar-cli` 的薄壳,不含业务逻辑:

| 工具 | 作用 |
|---|---|
| `cli_docs` | 返回 [cli.md](cli.md) 全文——完整命令契约,agent 先读它学习用法 |
| `cli_run` | 执行一条 polyscholar-cli 命令(白名单子命令、参数列表直传不经 shell、30 分钟超时、输出上限 100 KiB),返回退出码与输出 |

软件用途写在 server 的 instructions 里,agent 连接即知。CLI 新增命令时本 server **零改动**。

## 安装

```sh
pip install "polyscholar[mcp]"
```

即安装 `mcp==2.3.0`(官方 SDK,含传递依赖)。

## 配置示例

Claude Desktop(`claude_desktop_config.json`)与多数客户端同型:

```json
{
  "mcpServers": {
    "polyscholar": {
      "command": "polyscholar-mcp",
      "env": {
        "POLYSCHOLAR_DATA_DIR": "D:\\path\\to\\PolyScholar\\data"
      }
    }
  }
}
```

- `POLYSCHOLAR_DATA_DIR`(可选):指向资料目录。默认:源码运行为仓库 `data/`(便携),与 GUI 一致;冻结安装为系统应用目录。
- `POLYSCHOLAR_CLI_MD`(可选):cli.md 的替代位置。
- `POLYSCHOLAR_RUBRICS_DIR`(可选):打分文档(rubrics)目录的替代位置;默认按 cli.md→仓库→冻结包资源同链定位。
- `HTTPS_PROXY`/`HTTP_PROXY`(可选):`add --arxiv` 的联网走标准代理环境变量;需要代理的机器在这里设置后重启客户端。

Cursor 等(Chat 形式)同理:`command` = `polyscholar-mcp`(或 `python -m polyscholar.mcp_server`)。

## 路径自动定位(无需手动配置)

- **cli.md**:server 自动定位——环境变量 `POLYSCHOLAR_CLI_MD` > 源码仓库根 > 冻结包资源。
- **数据目录**:CLI 子进程自动使用与 GUI 相同的目录(源码运行 = 仓库 `data/`;安装版 = 系统应用目录);需要指向别处时才设 `POLYSCHOLAR_DATA_DIR`。
- 分发方式为 GitHub(克隆仓库或下载安装包):克隆运行自动使用仓库内 cli.md 与 data/;安装包自动携带 cli.md 并使用系统应用目录。不存在需要手动指定路径的常规场景。

## 单实例约束(重要)

GUI 与 MCP 共用同一资料目录的单实例锁:**GUI 开着时,MCP 的命令会失败**,错误信息为"此文献库已在运行"。当前版本请先关闭 GUI 再让 agent 操作;带后台调度服务的多进程并发是后续架构项。

## 安全边界

- `cli_run` 只放行 cli.md 列出的子命令(add/list/show/update/remove/text/parse/collection/translate/jobs/export-translation/score/rubric/status/search);参数以列表直传,不经 shell。
- `remove --yes` 会真实删除文献条目与受管副本(外部原文件保留);agent 调用前应向用户确认,或依赖 MCP 客户端的工具确认机制。
- 没有遥测;所有操作写入本机审计日志。

## 评分工作流的工具支撑

- `rubric list` / `rubric show paper|confidence`:agent 直接经 MCP 获取打分文档全文、版本与 SHA-256(冻结材料包必需),不需要在文件系统里找 rubrics 目录。
- `score validate`:写库前的结构校验(维度和=total、三个子代理、中位数一致、极差超限必须复核),失败逐条返回原因,退出码 1。
- GUI"查看完整评分明细"渲染的就是 `score show --json` 里 `detail` 字段的完整内容;两个入口看到的永远一致。

## 已知限制

- 真实 LLM 翻译的费用由你的 API 密钥承担;`translate` 全文可能持续数分钟,建议 agent 用 `translate --no-wait` + `jobs` 轮询。
- 评分/提炼的"智能"来自 agent 侧(读 `rubrics/` 打分文档后分析);程序只负责校验与存储。
