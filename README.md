# PolyScholar · 研译

**面向科研学生的个人本地双语文献工作台。** 管理文献、阅读论文、保留版面翻译、整理笔记与导出引用，在一个桌面应用里完成。

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-Native%20Desktop-23614f.svg)](docs/03-architecture.md)
[![Checks](https://github.com/Kelvin-LH/PolyScholar/actions/workflows/core.yml/badge.svg)](https://github.com/Kelvin-LH/PolyScholar/actions/workflows/core.yml)

> **研发中，尚未发布安装包。** 最新技术路线统一为 **Python + PySide6 原生桌面 + SQLite**；已完成基础原生界面和本地服务迁移。下方为界面设计效果图，并非已验收的软件截图。完整论文翻译质量、真实模型 API 和 Windows/Linux 安装包尚未验证。

## 界面效果图

白色工作区、深绿操作按钮、固定侧栏，让文献库、阅读、翻译与引用共享一条工作流。

### 文献库

以 Zotero 为功能对标：逐步完善集合、标签、元数据、附件、搜索、重复检测和参考文献交换。当前基础能力与后续差距见 [对标矩阵](docs/12-zotero-parity.md)。

![文献库界面设计效果图](design/concepts/library.png)

### 双语阅读

原文与译文并排阅读，笔记保存到本机；段落对齐、图中文字注释仍在规划中。

![双语阅读界面设计效果图](design/concepts/reader.png)

### 翻译任务

集成 BabelDOC 与 PDFMathTranslate。模型 API 外发和模型/字体下载默认允许，无需每次手动勾选；远程请求可能计费。翻译完成后可将译文 PDF 导出到指定位置。

![翻译任务界面设计效果图](design/concepts/tasks.png)

<details>
<summary>查看证据摘要、引用导出与设置界面</summary>

**证据摘要（规划）**：摘要关联原文页码与段落，避免把无依据的模型输出当作论文证据。

![证据摘要界面设计效果图](design/concepts/summary.png)

**引用导出**：BibTeX / RIS / CSL-JSON 元数据交换；完整 CSL 样式排版为后续能力。

![引用导出界面设计效果图](design/concepts/citations.png)

**设置**：配置模型 API、自动获取可用模型名称、调整缓存目录。发行包自带 Python，普通用户无需安装或选择解释器。

![设置界面设计效果图](design/concepts/settings.png)

</details>

## 原生界面当前截图

以下是 Python / PySide6 原型实际渲染截图（macOS 离屏运行，示例条目为合成测试 PDF），不代表完整功能验收。

![Python 原生文献库截图](design/screenshots/library-python.png)

![Python 原生设置截图：获取可用模型、缓存目录与自带运行环境](design/screenshots/settings-python.png)

## 使用方式与数据边界

- **个人原生桌面应用**，目标支持 Windows、macOS、Linux；无需部署服务器或创建账户。
- 应用代码统一使用 Python，界面采用 PySide6，数据库使用 SQLite。翻译引擎在独立进程与独立依赖环境中运行。
- 发行包将内置 Python 与引擎运行环境；已有 macOS arm64 两引擎独立运行环境的版本与迁移检查，本地 macOS arm64 开发包已生成并通过启动检查，尚未发布下载。
- 文献、笔记、数据库、缓存和译文保存在本机；缓存目录可调整，原始文献与译文分开保存。
- 允许联网的业务仅包括：配置的模型 API（如 DeepSeek 或兼容接口）、DOI 在线元数据查询、上游模型和字体下载。API 服务会接收所选翻译内容，可能产生费用。

不做学术搜索平台、盗版下载、论文代写、科学结论自动判真、学生监控、私人文献公开、多用户 SaaS、浏览器产品、团队服务或账户云同步。详见 [本地与联网范围](docs/09-local-and-network-scope.md)。

## 当前研发状态

| 模块 | 状态 |
|---|---|
| 六个核心界面 | 设计效果图与基础原生 Python 界面已完成 |
| BabelDOC / PDFMathTranslate | 已有真实 CLI 适配、独立环境、失败与取消处理；未验收真实论文翻译 |
| 自带 Python | macOS arm64 本地开发包已包含运行环境并通过启动检查；尚未签名、公证或发布 |
| 本地文献管理 | Python 本地服务已实现导入去重、元数据与笔记；合成 PDF 阅读检查通过 |
| 译文导出与缓存目录 | Python 服务已实现导出与缓存切换，边界测试通过 |
| 模型名称 | 通过兼容接口获取、选择或手动输入；协议测试通过，真实服务待验收 |
| Zotero 对标 | 分阶段实现，尚未达到完整功能对齐 |
| 证据摘要 / 图文注释 / CSL 样式 | 规划中，不提供伪造结果 |

开发者可运行原生界面：

```sh
python3 -m pip install -e .
python3 -m polyscholar
```

译文引擎的独立运行环境准备与发行构建见 [嵌入 Python](integrations/EMBEDDED_RUNTIME.md)。

## 开源许可与来源追踪

本项目采用 **[AGPL-3.0-only](LICENSE)**，允许商业使用，并要求遵守适用的源码、许可与通知义务；不附加禁止商用条款。个人私有文献、API 密钥和笔记不属于需要公开的项目源码。

保留上游许可与来源，使用本地来源清单记录版本和完整性。来源清单不上传私人材料，也不能自动认定违规商用。详见 [许可与追踪](docs/05-licensing-and-traceability.md) 和 [第三方声明](THIRD_PARTY_NOTICES.md)。

## 文档与参与开发

| 文档 | 内容 |
|---|---|
| [现状与定位](docs/01-landscape.md) | 用户问题与现有工具差距 |
| [需求与验收](docs/02-requirements.md) | 功能范围、优先级、验收指标 |
| [架构与语言](docs/03-architecture.md) | Python 原生桌面架构与数据边界 |
| [翻译与 AI](docs/04-document-and-llm.md) | 版面、图文、模型与证据摘要 |
| [引擎集成](docs/08-engine-integration.md) | 依赖隔离、适配与限制 |
| [界面规范](docs/10-ui-spec.md) | 六个界面、空状态与错误交互 |
| [研发计划](docs/11-development-plan.md) | 分工、阶段与验收 |
| [Zotero 对标](docs/12-zotero-parity.md) | 能力矩阵与实际差距 |
| [验证记录](docs/07-validation.md) | 已验证与未验证的能力 |

开发检查（需要开发者安装 Python；最终用户安装包不要求自行安装）：

```sh
python3 scripts/check_baseline.py
python3 -m unittest discover -s tests -v
```

请阅读 [贡献说明](CONTRIBUTING.md) 与 [安全说明](SECURITY.md)。公开 issue 和测试材料请勿包含私人论文或密钥。

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Kelvin-LH/PolyScholar&type=Date)](https://www.star-history.com/#Kelvin-LH/PolyScholar&Date)

图表由 [Star History](https://github.com/star-history/star-history) 根据 GitHub 公开 Star 数据生成；新仓库数据较少或服务缓存时，曲线可能暂时为空。

## English

PolyScholar is a personal, local desktop workspace for bilingual research reading and reference management. The application is being developed entirely in Python with PySide6 and SQLite, integrating BabelDOC and PDFMathTranslate in isolated runtimes. Packaged releases will include Python. Design images show the intended UI; production installers and full feature parity with Zotero are not available yet. Licensed under AGPL-3.0-only.
