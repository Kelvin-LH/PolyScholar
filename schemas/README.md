# 数据模型状态

`001_initial.sql` 是未来 DocumentIR、翻译块、摘要证据与引用模型的参考设计，当前桌面程序不加载它。其约束测试只验证设计，不代表证据摘要已经落地。

当前运行时由 `polyscholar/store.py` 管理 SQLite v3：desktop_documents/settings/jobs/audit、desktop_collections/memberships。集合关系使用外键与事务，文献与任务字段仍部分保存在 JSON 中。v1 升级集合前保存 v2 前备份；v1/v2 升级审计约束前保存 v3 前备份。升级保留文献、附件和已有审计历史，新审计写入使用 Python 枚举和 SQLite 触发器约束。

下一步须将解析版本、页码、块坐标和证据外键接入实际运行时迁移，再实现摘要来源查询与失效定位；不能拿参考 SQL 的测试替代这一步。
