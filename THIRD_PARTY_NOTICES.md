# 第三方来源与许可

核查更新：2026-10-02。

| 组件 | 固定集成版本 | 来源 / 许可 |
|---|---|---|
| BabelDOC | 0.6.4 | [v0.6.4](https://github.com/funstory-ai/BabelDOC/tree/v0.6.4)，AGPL v3；许可副本 licenses/upstream/BabelDOC-v0.6.4-LICENSE |
| PDFMathTranslate / pdf2zh | 1.9.11 | [v1.9.11](https://github.com/PDFMathTranslate/PDFMathTranslate/tree/v1.9.11)，AGPL v3；许可副本 licenses/upstream/PDFMathTranslate-v1.9.11-LICENSE |
| 腾讯 TMT SDK | 3.1.70 | 为 pdf2zh 1.9.11 固定兼容导入；打包前保留其独立许可 |
| 旧 BabelDOC 传递依赖 | pdf2zh 环境解析得到的 0.2.x | 由 pdf2zh 固定的上游范围决定；不与独立 0.6.4 环境混装 |

本仓库包含原创 CLI 适配层、上游许可副本与版本要求，不将上游引擎源代码复制入原创目录。当前要求文件只固定顶层版本，不是完整哈希锁文件；所有传递依赖、模型和字体均需在发行包前完成 SBOM/许可/版本及来源校验。安装时依赖可能更新，所以不能将“顶层固定”宣传为可重复发行构建。

Docling、Tauri、React、PDF.js、PDFium、Tesseract、CSL 处理器均为后续候选，尚未集成。代码、模型、字体和引用样式各自许可，不能一概由本仓库 AGPL 覆盖。DeepSeek 等 API 是外部服务，适用服务条款、数据处理政策与费用。

GitHub Actions 使用官方 checkout 和 setup-python 发行标签；正式供应链硬化时固定经审查的完整 commit SHA。所有新增依赖保留版权、许可与适用 NOTICE；独立进程不自动免除分发/网络义务。
