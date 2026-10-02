<div align="center">

# PolyScholar · 研译

**你的本地双语文献工作台**

文献管理 · 论文翻译 · 双语阅读 · 笔记与引用

[![Checks](https://github.com/Kelvin-LH/PolyScholar/actions/workflows/core.yml/badge.svg)](https://github.com/Kelvin-LH/PolyScholar/actions/workflows/core.yml)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/badge/License-AGPL--3.0-blue)](LICENSE)
[![Stars](https://img.shields.io/github/stars/Kelvin-LH/PolyScholar?style=flat)](https://github.com/Kelvin-LH/PolyScholar/stargazers)

[界面预览](#界面预览) · [快速开始](#快速开始) · [开发路线](#开发路线) · [文档](#文档) · [参与贡献](#参与贡献)

</div>

> 开发中，尚未发布安装包。

![文献库设计](design/concepts/library.png)

## 核心功能

- **本地文献库**：集合与子集合、多 PDF 附件、四类文献信息、有序个人/机构作者、标签与笔记、高级检索和保存搜索，文献管理对标 Zotero。
- **双引擎翻译**：集成 [BabelDOC](https://github.com/funstory-ai/BabelDOC) 与 [PDFMathTranslate](https://github.com/PDFMathTranslate/PDFMathTranslate)，原文与译文分开保存。
- **双语阅读**：原文与译文并排显示，在桌面内完成阅读。
- **证据摘要**：勾选原文生成结构化模型摘要，保留引用与手写笔记，可跳转原文并提示旧引用失效。
- **自选模型**：支持 DeepSeek 等兼容 API，自动获取可用模型，也可手动填写。
- **文件导出**：导出译文 PDF，以及 BibTeX、RIS、CSL-JSON 文献元数据。
- **个人桌面**：Python + PySide6 + SQLite，无需服务器或账户。发行包自带 Python，缓存目录可调整。

文献与笔记保存在本机。联网用于模型 API、DOI 元数据查询及模型/字体下载；翻译时会向模型 API 发送论文内容。[数据边界 →](docs/09-local-and-network-scope.md)

## 界面预览

### 双语阅读 · 设计效果图

![双语阅读设计](design/concepts/reader.png)

### 翻译任务 · 设计效果图

![翻译任务设计](design/concepts/tasks.png)

<details>
<summary>更多界面设计</summary>

**证据摘要**

![证据摘要设计](design/concepts/summary.png)

**引用导出**

![引用导出设计](design/concepts/citations.png)

**模型与本地设置**

![设置设计](design/concepts/settings.png)

</details>

<details>
<summary>查看当前原生界面截图</summary>

Python / PySide6 原型，使用合成测试 PDF。

![原生文献库](design/screenshots/library-python.png)

![原生设置](design/screenshots/settings-python.png)

![原生文献附件](design/screenshots/attachments-python.png)

![原生书目信息](design/screenshots/metadata-python.png)

![作者与编者](design/screenshots/creators-python.png)

![高级检索与保存搜索](design/screenshots/searches-python.png)

![元数据检索条件](design/screenshots/search-rules-python.png)

![原生证据摘要与笔记](design/screenshots/evidence-python.png)

</details>

## 快速开始

当前可从源码运行，需 Python 3.12+。

```sh
git clone https://github.com/Kelvin-LH/PolyScholar.git
cd PolyScholar
python -m pip install -e .
python -m polyscholar
```

翻译引擎准备与自带 Python 打包见 [运行环境指南](integrations/EMBEDDED_RUNTIME.md)。

1. 导入本地 PDF。
2. 在设置中填写 API 地址与密钥，获取并选择模型。
3. 选择引擎与页范围，创建翻译任务。
4. 阅读或导出翻译结果。


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

## Star History

[![Star History](https://api.star-history.com/svg?repos=Kelvin-LH/PolyScholar&type=Date)](https://www.star-history.com/#Kelvin-LH/PolyScholar&Date)
