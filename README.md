# PolyScholar · 研译

**面向高校科研学生的本地优先双语文献工作台。**

将文献管理、原位双语阅读、图中文字注释、可追溯总结及引用格式管理放进同一工作流。支持设计中的 DeepSeek、其他兼容接口和本地模型。

> 当前状态：**需求与架构基线 + Rust 核心参考 + BabelDOC/PDFMathTranslate CLI 适配层**。不是可安装桌面应用；适配层可调用独立安装的真实上游引擎，但尚未验证完整论文翻译质量、OCR、付费模型或三平台桌面体验。
## 部署方式

**个人本地桌面应用，不采用 B/S 架构。** 文献、数据库、批注和操作记录保存在本机，无需服务器、账户或团队部署。Tauri 的打包界面通过本机 IPC 调用 Rust，API 翻译由客户端直接访问用户选定的服务。

## 项目目标

- 无需反复复制粘贴：选中段落或整篇翻译，与原文同步定位。
- 保留原文件：双语层和图中文字注释独立存储，失败时返回原文。
- 摘要每个关键结论都能回到原论文页码与段落。
- 文献、术语、批注和引用数据关联；以 CSL 处理引用样式，以 BibTeX/RIS/CSL-JSON 交换元数据。
- 首期 Windows/macOS/Linux 桌面端，仅个人本地桌面使用，无 B/S 服务端。

## 许可与商业使用

项目采用 **[GNU AGPL-3.0-only](LICENSE)**，允许商业使用，落实适用的相应源码公开义务。用户已明确接受此路线，以便集成 BabelDOC 和 PDFMathTranslate。**不存在“禁止未经授权商用”的附加限制。**

个人离线使用和自行修改不要求向公众发布；分发修改版/二进制时遵守适用的源码和通知义务。AGPL 的网络服务条款仅在实际发生相应行为时适用，本产品不设计网络服务。具体交付方式见 [许可与追踪](docs/05-licensing-and-traceability.md)。AGPL 不要求公开 API 密钥、私有论文、用户数据，也不等于公开整个组织所有软件。

BabelDOC 和 PDFMathTranslate 采用独立 Python 环境，避免其不同版本依赖冲突。保留上游许可及来源；不声称上游项目为本项目所有。图文和输出来源追踪不上传私人材料，不用于判断商用目的。

## 文档

| 文档 | 内容 |
|---|---|
| [现状与定位](docs/01-landscape.md) | 现有能力、真实差距、用户访谈与竞争路线 |
| [需求与验收](docs/02-requirements.md) | 用户故事、范围、优先级、验收指标 |
| [架构与语言](docs/03-architecture.md) | Rust/Tauri/TypeScript、跨平台、数据结构、安全边界 |
| [翻译与 AI 方案](docs/04-document-and-llm.md) | PDF/图文/OCR、模型协议、摘要证据、费用控制 |
| [许可与追踪](docs/05-licensing-and-traceability.md) | AGPL 义务、来源标记、隐私与技术限制 |
| [实施路线](docs/06-roadmap.md) | 阶段计划、任务清单、风险和成本 |
| [验证记录](docs/07-validation.md) | 已运行的检查与尚未验证的能力 |

## 仓库结构

```text
crates/polyscholar-core/  无第三方依赖的 Rust 领域参考实现
schemas/                SQLite 初始迁移与数据示例
integrations/           两个上游翻译引擎的独立进程适配
scripts/                来源清单生成/核查、基线检查
tests/                  Python 标准库测试
docs/                   中文产品与技术文档
.github/                核心三平台 CI、贡献任务模板
```

## 引擎集成

- BabelDOC 0.6.4：版面翻译引擎。
- PDFMathTranslate / pdf2zh 1.9.11：另一条独立翻译路径。
- 运行适配层前需自行配置模型、密钥和外发范围；第一版 CLI 尚无严格 token/费用预算网关。详见 [集成步骤和限制](docs/08-engine-integration.md)。

## 运行基线检查

需要 Python 3.9+；Rust 检查另需稳定版工具链。

```sh
python3 scripts/check_baseline.py
python3 -m unittest discover -s tests -v
python3 scripts/provenance.py generate --output /tmp/polyscholar-provenance.json
python3 scripts/provenance.py verify /tmp/polyscholar-provenance.json
cargo test --workspace
```

来源清单是未签名的完整性参考，不是授权令牌或官方发行证明。本工具不自动上传日志、论文或个人信息。`--output` 文件必须在仓库外，避免自身参与哈希。

## Contributing / English summary

PolyScholar is a planned local-first bilingual research workspace. The repository currently contains a product/architecture baseline, a small Rust domain reference, SQLite schema, and provenance tooling. It is licensed under **AGPL-3.0-only**, including commercial use subject to applicable source and notice obligations. There are no finished desktop installers or live LLM integrations yet.

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)、[SECURITY.md](SECURITY.md) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。上游集成安装与命令示例见 [引擎集成](docs/08-engine-integration.md)。公开 issue 请勿附私人论文或密钥。
