# BabelDOC / PDFMathTranslate 集成

采用官方 CLI 边界：BabelDOC 文档将内部 Python API 标为不稳定，因此不直接导入其内部翻译接口。两个引擎是可选择的替代路径，不依次翻译同一篇论文，避免重复成本与质量损失。

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
- 密钥不进入命令参数。BabelDOC 用任务临时 TOML；pdf2zh 用任务临时 JSON。
- pdf2zh 会持久化 translator 配置，必须使用 `--config` 指向临时私有文件；上游初始化可能创建默认空配置文件，但本适配不把密钥传入环境变量。
- 临时文件/目录退出时清理；普通 POSIX 文件权限为 0600，Windows ACL 与崩溃残留清理仍须专门验证。明文临时文件不是 OS 凭据库的最终方案。
- 子进程输出不回显，避免原始日志泄露论文/密钥；失败只报状态。后续支持经过用户预览的脱敏诊断。
- 超时或中断终止进程树；已经到达远程的请求仍可能收费。进程隔离不是完整沙箱。
- 未实现严格 token 预算、usage 汇总、断点续传和统一 LLM 网关；上游重试可能产生重复费用。生产产品集成前必须补齐。

## 状态边界

适配层参数、临时配置、超时、输出哈希和隐私边界由合成引擎测试覆盖。真实上游安装与 CLI 版本检查记录在 docs/07-validation.md。合成测试不证明整篇 PDF 能无损翻译，也不证明实际模型或上游下载行为满足隐私承诺。

上游：[BabelDOC](https://github.com/funstory-ai/BabelDOC)、[PDFMathTranslate](https://github.com/PDFMathTranslate/PDFMathTranslate)。许可按 AGPL 原文执行；本项目不限制合法商用。
