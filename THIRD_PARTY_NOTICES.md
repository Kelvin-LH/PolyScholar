# 第三方来源与许可

核查更新：2026-10-02。

| 组件 | 固定集成版本 | 来源 / 许可 |
|---|---|---|
| BabelDOC | 0.6.4 | [v0.6.4](https://github.com/funstory-ai/BabelDOC/tree/v0.6.4)，AGPL v3；许可副本 licenses/upstream/BabelDOC-v0.6.4-LICENSE |
| PDFMathTranslate / pdf2zh | 1.9.11 | [v1.9.11](https://github.com/PDFMathTranslate/PDFMathTranslate/tree/v1.9.11)，AGPL v3；许可副本 licenses/upstream/PDFMathTranslate-v1.9.11-LICENSE |
| 腾讯 TMT SDK | 3.1.70 | 为 pdf2zh 1.9.11 固定兼容导入；打包前保留其独立许可 |
| 旧 BabelDOC 传递依赖 | pdf2zh 环境解析得到的 0.2.x | 由 pdf2zh 固定的上游范围决定；不与独立 0.6.4 环境混装 |
| CPython / python-build-standalone | 3.12.14 / 20260929 | [官方发行](https://github.com/astral-sh/python-build-standalone/releases/tag/20260929)，保留独立 tree 自带 CPython 与内置第三方许可；固定资产 SHA-256 见 scripts/prepare_runtime.py |
| PySide6 / Shiboken6 / Qt | 6.11.2 | [PySide6 对应源码](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/)，本项目使用开源许可路径；LGPL-3.0/GPL-3.0 与各文件许可适用，许可副本见 licenses/desktop/ |
| PyMuPDF / MuPDF | 1.28.2 | [对应源码与许可](https://github.com/pymupdf/PyMuPDF/tree/1.28.2)，采用 AGPL 开源许可；副本 licenses/desktop/PyMuPDF-1.28.2-COPYING.txt；本地文本解析，不调用 OCR 或模型 |
| PyInstaller | 6.19.0 | [官方许可](https://pyinstaller.org/en/v6.19.0/license.html)，GPL-2.0 附打包例外，部分文件 Apache-2.0；副本 licenses/desktop/PyInstaller-6.19.0-COPYING.txt |

本仓库包含原创 CLI 适配层、上游许可副本与版本要求，不将上游引擎源代码复制入原创目录。当前要求文件只固定顶层版本，不是完整哈希锁文件；所有传递依赖、模型和字体均需在发行包前完成 SBOM/许可/版本及来源校验。安装时依赖可能更新，所以不能将“顶层固定”宣传为可重复发行构建。

主桌面采用 Python + PySide6 QtWidgets/QtPdf。QtPdf 内部包含 PDFium 等第三方代码，它们保留各自许可；不得把 Qt 全部代码笼统宣称为 LGPL。Qt 动态库在发行目录中独立存在，无静态链接；源码及构建脚本公开，用户可替换其修改版本并为调试修改而逆向分析，本项目不施加禁止此类操作的额外条款。具体替换路径与对应源码提供安排见 [发行许可说明](licenses/desktop/DISTRIBUTION.md)。当前 PyInstaller 默认 Qt 插件还会带入 QtVirtualKeyboard（GPL-3.0 路径），该部分与 AGPL-3.0 项目兼容但必须单独落实源码与版权提供。正式发布前应裁减未使用插件或完成其完整源码分发。

Docling、Tesseract、CSL 处理器尚未集成。原 Tauri/React 桌面草稿已由纯 Python 原生桌面方案替代。代码、模型、字体和引用样式各自许可，不能一概由本仓库 AGPL 覆盖。DeepSeek 等 API 是外部服务，适用服务条款、数据处理政策与费用。

GitHub Actions 使用官方 checkout 和 setup-python 发行标签；正式供应链硬化时固定经审查的完整 commit SHA。所有新增依赖保留版权、许可与适用 NOTICE；独立进程不自动免除分发/网络义务。
