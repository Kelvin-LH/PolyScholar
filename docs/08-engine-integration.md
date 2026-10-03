# BabelDOC / PDFMathTranslate 集成

联网范围允许模型 API、DOI 元数据查询及上游模型/字体下载；文献处理与保存仍在本机。详细要求见 [个人使用与联网范围](09-local-and-network-scope.md)。此 CLI 尚未实现 DOI 查询，也未验证所有上游网络载荷；不得将允许下载解释为允许云 OCR、文献云存储或遥测。

保留固定版本上游 CLI 的翻译流程，由可信 Python 入口提供内存配置。入口只开放本应用已有的参数，不启动上游 GUI、MCP 或服务模式；解析器与配置管理的适配依赖固定版本，升级时必须重新验证。两个引擎是可选择的替代路径，不依次翻译同一篇论文。

## 独立环境

用 Python 3.12 建两个环境。BabelDOC 0.6.4 需要较新 PyMuPDF；pdf2zh 1.9.11 要求 PyMuPDF <1.25.3 和 BabelDOC <0.3.0，因此不能共享环境。

```sh
python3.12 -m venv .venvs/babeldoc
python3.12 -m venv .venvs/pdfmathtranslate
.venvs/babeldoc/bin/python -m pip install -r integrations/babeldoc-requirements.txt
.venvs/pdfmathtranslate/bin/python -m pip install -r integrations/pdfmathtranslate-requirements.txt
```

Windows 将 `.venvs/<name>/bin/python` 替换为 `.venvs/<name>/Scripts/python.exe`。pdf2zh 环境额外固定腾讯 TMT SDK 3.1.70，修复新版本删除旧导入的问题。生产安装需固定传递依赖、验证下载哈希，并按平台测试模型与字体包。

## 调用

适配层读取 `POLYSCHOLAR_API_KEY` 环境变量（请在本机安全配置；不把真实密钥写入 shell 历史、仓库或公开问题）。对于明确的本地网关，可使用 dummy token。模型名按提供商实际能力填写。

```sh
python3 -m integrations.engines --engine babeldoc \
  --python .venvs/babeldoc/bin/python --input /absolute/path/paper.pdf \
  --output /absolute/path/new-output-directory \
  --endpoint https://api.deepseek.com --model YOUR_CONFIGURED_MODEL \
  --pages 1-3 --allow-document-upload --allow-asset-download
```

换引擎时改为 `--engine pdfmathtranslate --python .venvs/pdfmathtranslate/bin/python`。输出目录必须不存在；页范围为 1-based，包括末页。成功结果附 `polyscholar-export.json`：引擎版本、模型、语言、页范围、输入/输出哈希。该清单未签名，不能证明来自官方发行，也不能证明翻译正确。

显式确认标志表示允许指定引擎将选定内容发往配置 endpoint，并允许上游下载模型/字体等资源。上游可能读取/解析整篇 PDF，即使仅选部分页翻译；当前尚未通过网络捕获验证严格页范围外发，因此保密材料不要使用此 CLI。没有密钥就不会实际发起付费测试。

## 凭据与执行防护

- 使用绝对路径和 argv 列表，不启动 shell；页范围、语言和 endpoint 校验。
- 桌面、worker 和可信引擎入口之间通过有界 stdin 传递密钥，不进入子进程命令参数或环境，不生成含密钥的配置文件。
- BabelDOC 使用真实解析器读取内存 TOML；pdf2zh 在加载高层模块前安装内存配置管理，复用原配置 API，阻止默认配置读取及 translator 自动保存。真实上游翻译仍使用原入口。
- 临时工作目录仍可包含上游中间文件，正常退出时清理；无法确认进程树退出则保留并报告失败。旧版本凭据残留恢复、真实引擎硬退出、Windows ACL 和 OS 凭据库继续作为独立门槛。
- 子进程输出不回显，避免原始日志泄露论文/密钥；失败只报状态。后续支持经过用户预览的脱敏诊断。
- 超时或中断终止进程树；已经到达远程的请求仍可能收费。进程隔离不是完整沙箱。
- 未实现严格 token 预算、usage 汇总、断点续传和统一 LLM 网关；上游重试可能产生重复费用。生产产品集成前必须补齐。

## 状态边界

适配层参数、管道交付、超时、输出哈希和隐私边界由合成引擎测试覆盖。`scripts/smoke_engines.py` 在隔离合成目录中阻断网络，检查固定版本解析器、内存配置和客户端构造；替换最后的翻译调用，不执行模型请求。真实上游安装与其他验证记录见 [验证记录](07-validation.md)。这些检查不证明整篇 PDF 能无损翻译，也不证明实际模型或上游下载行为满足隐私承诺。

上游：[BabelDOC](https://github.com/funstory-ai/BabelDOC)、[PDFMathTranslate](https://github.com/PDFMathTranslate/PDFMathTranslate)。许可按 AGPL 原文执行；本项目不限制合法商用。
