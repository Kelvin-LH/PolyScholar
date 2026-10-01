# 原生桌面发行许可记录

Copyright (C) The Qt Company Ltd. and other contributors applies to Qt, PySide6 and Shiboken6. 各源文件与上游 NOTICE 中的具体版权保留。Qt/PySide6 6.11.2 来自未修改的官方 wheels；PyInstaller 只改变发行布局和部分动态库加载路径，没有修改 Qt/PySide6 源代码。原始源码及构建说明由相应上游版本提供。

本仓库提供 LGPL-3.0 与 GPL-3.0 完整文本。Qt/PySide6 动态链接组件与应用逻辑分开放置；用户可为修改和调试这些组件而替换、重链接或逆向分析。macOS 动态库目录为 `PolyScholar.app/Contents/Frameworks/`，Windows/Linux 为 `PolyScholar/_internal/`。替换库必须保持相同 ABI，并按需要对用户自己的构建重新签名；本项目不使用签名验证或在线许可阻止替换。使用 GitHub 原创应用源码、pyproject.toml、scripts/package_desktop.py 可重建主程序，以装载自行构建的对应 Qt/PySide6。两份引擎独立 runtime 的全部上游许可与 `.dist-info` 目录随 tree 一同保留。

6.11.2 对应源码位置：

- [PySide6/Shiboken6 官方源码归档](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.tar.xz)
- [Qt 6.11.2 官方组件归档索引](https://download.qt.io/official_releases/qt/6.11/6.11.2/submodules/)：qtbase（Core/Gui/Widgets/Network/DBus/OpenGL）、qtdeclarative（Qml/Quick）、qtsvg、qtwebengine（Pdf/PdfWidgets 与 PDFium/Chromium 第三方来源）、qtvirtualkeyboard（GPL-3.0 默认插件）。
- [Qt/PySide6 构建说明](https://doc.qt.io/qtforpython-6/building_from_source/index.html)
- [Qt 第三方版权与许可记录](https://doc.qt.io/qt-6/licenses.html)

这份文件记录当前本地开发包的替换权利与源码定位，**不将仅列出 URL 视为已完成全部二进制分发义务**。本地开发 `.app` 尚未公开上传发行。向公众发布安装包时，发布者必须在同一发行位置提供使用版本的完整对应源码、适用构建配置、全部第三方版权/许可/SBOM 与重新构建说明，或实施许可原文允许的有效源码提供安排；包含 GPL 模块和 AGPL 引擎时同样落实其对应义务。不得仅把本文件当作替代完整源码提供的许可证明。

PyInstaller 6.19.0 使用 GPL-2.0 配打包例外，允许按应用及依赖自己的许可分发产物。保留其 COPYING.txt 作为构建来源记录。CPython 3.12.14 与独立 tree 的内置第三方许可随原发行保留，不由本项目 AGPL 替换。
