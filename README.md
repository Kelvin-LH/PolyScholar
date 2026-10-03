<div align="center">

# PolyScholar · 研译

**本地论文库 × 可核验的 AI 评分工作台**

让 AI agent 按量化量表读论文、盲评打分、结构化提炼——每个分数都能翻回原文核对。

[![Checks](https://github.com/Kelvin-LH/PolyScholar/actions/workflows/core.yml/badge.svg)](https://github.com/Kelvin-LH/PolyScholar/actions/workflows/core.yml)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/badge/License-AGPL--3.0-blue)](LICENSE)

[AI 评分工作流](#ai-评分工作流核心) · [三种用法](#三种用法同一套能力) · [快速开始](#快速开始) · [开发路线](#开发路线)

</div>

> 开发中，尚未发布安装包。

## 为什么是这个样子

读论文最贵的不是下载，是**判断**：这篇论文的创新是否成立？结论可信到什么程度？PolyScholar 把"判断"做成可核验的工作流，而不是一句模型印象：

- **评分不是打印象分**——两份量化量表（[论文质量](rubrics/paper-scoring.md) 20 个条目、[置信度](rubrics/confidence-scoring.md) 四维红旗），每个条目按连续条件计分，必须引用原文位置；
- **不是单模型说了算**——三个互相不可见的子代理盲评，取中位数；极差超过 10/15 分强制复核，复核后仍有分歧如实标注；
- **程序强制规则，agent 只做判断**——结构校验、中位数、材料包一致性、复核门槛全部由程序执行；agent 试图提交不合规范的评分会被直接拒绝；
- **结论可溯源**——文献详情页能查到每个维度"得在哪里、卡在哪一条、依据原文哪一句"，三位评委的逐字原文永久留档。

## AI 评分工作流（核心）

```sh
polyscholar-cli rubric show paper            # agent 先取打分文档(含版本与 SHA-256)
polyscholar-cli parse <doc>                  # 解析全文
polyscholar-cli text <doc> --pages 2-5       # 按页阅读,search 可全文定位
# 你的 AI agent 开三个隔离子代理,各写一份评分报告 JSON
polyscholar-cli score aggregate --kind paper \
    --reports A.json B.json C.json --apply <doc> --rationale-file r.md
```

极差超限时 aggregate 不合卷，返回**分歧条目清单 + 复核指令模板**，复核后加 `--recheck` 重跑。提炼同理，只需两个子代理：

```sh
polyscholar-cli score aggregate --kind summary \
    --reports A.json B.json --apply <doc>    # 四节:问题/方法/实验效果/不足
```

桌面上每篇文献显示**论文分 / 置信度 / AI 提炼**三块独立窗口；"查看完整评分明细"按选项卡展开全部维度得分、得失分点、红旗清单和评委原文，可导出 JSON。

## 三种用法（同一套能力）

| 入口 | 给谁用 | 说明 |
|---|---|---|
| **桌面端** | 人 | PySide6 原生界面：文献库、双语阅读、评分与提炼展示 |
| **命令行** | 脚本 | `polyscholar-cli`，全部命令支持 `--json`，契约见 [cli.md](cli.md) |
| **MCP** | AI agent | 两个工具（`cli_docs`/`cli_run`）接入 ZCode/Claude/Cursor，见 [mcp.md](mcp.md) |

三种入口调用同一个服务层——同样的校验、同样的审计、同一份数据。

## 文献库与阅读

- **本地文献库**：集合与子集合、多 PDF 附件、五类文献类型、有序作者、标签、高级检索、保存搜索，对标 Zotero 的个人工作流；
- **双语翻译**：arXiv 论文一键生成双语 HTML，保留图表与公式，译文归属文献、可导出；
- **全文检索**：解析后支持库级/单篇中文全文定位（`search` 命令），复核评分时免整篇重读；
- **元数据导出**：BibTeX / RIS / CSL-JSON。

## 设计原则

- **本地优先**：SQLite 存本机，无账户无云同步；联网只发生在你触发的动作（[边界说明](docs/09-local-and-network-scope.md)）；
- **程序化优先**：能由程序确定完成的（校验、合卷、合并、归档）绝不让模型重做——省 token，也省出错面；
- **诚实呈现**：没有的能力不假装——空态说明如何产生数据，核验不了如实标注"无法核验"。

## 快速开始

需 Python 3.12+。

```sh
git clone https://github.com/Kelvin-LH/PolyScholar.git
cd PolyScholar
python -m pip install -e .
python -m polyscholar
```

1. 导入本地 PDF 或粘贴 arXiv 链接；
2. （可选翻译）设置模型 API，创建翻译任务；
3. （可选评分）接入 MCP 后让 agent 按 rubrics 完成评分与提炼——见 [mcp.md](mcp.md)。

## 开发路线

UI 简化重构、评分的联网核验（研究进度/SOTA 感知）、置信度的开源真实性核验（空壳仓库/部分开源/代码质量）等方向整理在 [docs/16-roadmap.md](docs/16-roadmap.md)。

## 参与贡献

欢迎提交 [Issue](https://github.com/Kelvin-LH/PolyScholar/issues) 或 Pull Request。提交前请阅读 [贡献指南](CONTRIBUTING.md)。

```sh
python scripts/check_baseline.py
python -m unittest discover -s tests -v
```

安全问题请参阅 [SECURITY.md](SECURITY.md)。

## 致谢与许可

[AGPL-3.0-only](LICENSE) · 允许商业使用，遵守适用的源码公开与通知义务。

[第三方许可](THIRD_PARTY_NOTICES.md) · [来源追踪](docs/05-licensing-and-traceability.md)
