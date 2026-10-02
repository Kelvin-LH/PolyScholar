# PolyScholar development conventions / 开发约定

- Keep the application Python, native PySide6 desktop and local SQLite. No web server or cloud storage. / 应用坚持 Python、原生 PySide6 桌面及本地 SQLite，不引入 Web 服务或云存储。
- Organize domain, persistence, integration and UI responsibilities into focused classes. Share validation and business operations instead of copying them across pages. / 按领域、存储、集成和界面划分类职责，校验与业务操作复用，不跨页面复制。
- Explain non-obvious invariants, failure handling and lifecycle decisions with concise Chinese and English comments or docstrings. Preserve SPDX headers and protocol identifiers. / 对非显然的不变量、失败处理及生命周期提供简洁中英文注释或文档字符串，保留 SPDX 头和协议标识。
- Prefer readable multiline code. Keep pure transformations separate from IO and avoid unnecessary abstraction. / 优先清晰的多行代码，纯转换与 IO 分开，避免无必要的抽象。
- Preserve private data boundaries, transaction atomicity and managed worker shutdown. Verify meaningful failure paths and affected native flows. / 保持隐私边界、事务原子性和工作线程关闭管理，验证实际失败路径及受影响的原生流程。
- Never equate source tests with real engine/API or installed-package acceptance. Record remaining gates in docs/14-review-remediation.md. / 不把源码测试等同真实引擎/API或安装包验收，未通过门槛记录在整改文档。
