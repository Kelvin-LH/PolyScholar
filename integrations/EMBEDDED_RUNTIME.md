# 自带 Python 与双引擎资源

最终用户不安装 Python、不创建虚拟环境、不配置解释器路径。维护者构建时执行 `python3 scripts/prepare_runtime.py`，默认生成被 Git 忽略的 `.runtime/`；制品将这棵树作为只读应用资源带入安装包，不在用户启动时运行 pip。

```text
resources/runtime/
  runtime-manifest.json
  babeldoc/
    bin/python3                 # macOS/Linux，目录内相对 symlink 指向同包 CPython
    lib/python3.12/...
    installed-packages.txt
  pdfmathtranslate/
    bin/python3
    lib/python3.12/...
    installed-packages.txt
resources/integrations/
  job_worker.py
  engines.py
```

Windows 对应 `runtime/<engine>/python.exe`。采用两份独立 CPython tree，依赖分别安装；避免两个上游的 PyMuPDF/BabelDOC 版本冲突，也避免外部 PYTHONPATH 和用户 site-packages。`bin/python3` 如为链接，目标必须在该独立 tree 内；不得复制依赖系统 Python 的 venv。后端从可信资源路径选择 binary，以 `-I` 做版本探测；worker 脚本按可信绝对资源路径调用，不能由 UI 提供 packagePath 或自由解释器路径。开发环境优先 `.runtime/<engine>/bin/python3`，`.venvs/` 仅兼容旧开发路径，不作为发行依赖。

CPython 固定官方 [python-build-standalone 20260929](https://github.com/astral-sh/python-build-standalone/releases/tag/20260929) 的 3.12.14 install_only 资产，脚本内分别固定平台 SHA-256。2026-10-02 通过官方仓库 GitHub API 读取资产 digest，构建下载验证后解包。没有关闭 TLS 校验。脚本拒绝越出解包根目录的路径与链接。引擎直接依赖版本固定；构建记录完整 installed-packages.txt，传递依赖尚未全面实现 hash lock，不能据此宣称完全可复现。

脚本在安装后将整个独立 tree 移动到另一个前缀，用迁移后 Python 执行版本检查、真实上游 `--version` 与 `pip check`，再移回正式目录重复检查。执行检查的 PATH 仅有内置 runtime bin（Windows 为 runtime 根），不依赖系统 Python或用户 site-packages；不使用真实论文、API、OCR 模型下载。

资源选择矩阵已写入脚本：macOS arm64/x86_64、Linux glibc aarch64/x86_64、Windows ARM64/AMD64。跨平台必须各自原生构建、打包、验证；存在资产不等于该平台的上游 wheels 可用或安装包已通过验收。Linux musl 不在当前范围。macOS arm64 实际构建结果由本次验证报告记录，Windows/Linux 不宣称已测。

本次已验证的 macOS arm64 两份 tree 共约 1.8 GB（未压缩）。部分当前原生依赖 wheel 的最低 macOS 版本为 15，构建主机为 macOS 27.0；因此本次制品不能宣称兼容 macOS 13/14。正式发行应锁定兼容目标与全部依赖，并在最低支持系统实测。

主程序采用 Python + PySide6 QtWidgets/QtPdf 原生桌面。PySide6 6.11.2 已从官方 Qt 发行索引和仓库标签核验；Qt 文档支持使用 PyInstaller 发行。构建环境执行 `.venvs/desktop/bin/python scripts/package_desktop.py`。PyInstaller 6.19.0 冻结主程序与 Qt，之后复制两份完整独立引擎 runtime，保持可执行权限、相对链接和上游许可文件。最终位置 macOS 为 `PolyScholar.app/Contents/Resources/resources/`，Windows/Linux 为 `PolyScholar/_internal/resources/`。主程序根据冻结环境从可信资源目录选择引擎；禁止用户配置解释器路径。脚本再次在最终安装前缀执行引擎版本验证，不把 builder 本机 Python 当作用户依赖。

Python/依赖资源必须随发行保留各自许可和版权文件，macOS 签名、公证需涵盖内置动态库。脚本可接收维护者 `--codesign-identity`，未提供时只得到开发制品，不宣称正式签名/公证或跨平台已测。模型/字体是后续本机缓存资源，与 Python 解释器打包区分；按用户默认授权策略准备资源。app 的用户数据库、密钥、结果和缓存写入 app_data/cache，不能写入只读应用资源目录。

Linux 原生 Qt 启动需要系统图形库。Ubuntu CI 已发现并显式安装 `libegl1`；发行包需要列出/提供对应平台依赖，而不是要求用户安装 Python。Windows/macOS 原生启动已在 CI 通过，完整安装发行仍待验收。
