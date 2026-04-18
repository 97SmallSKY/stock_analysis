# ADR 0001:放弃 SQLAlchemy ORM 与 Alembic

- 状态:已生效(追认)
- 日期:2025-04-18(决策实际发生在 W1~W2 期间,本文为事后补录)
- 相关:docs/重写方案_v2.md

## 背景

项目初期计划使用 SQLAlchemy ORM + Alembic 做数据层与迁移管理。实际开发中发现:

1. 本项目数据模型以宽表 + 时间序列为主,ORM 带来的抽象收益低。
2. 批量写入场景下 ORM 性能显著低于直接使用 DB-API / pandas to_sql。
3. Alembic 的迁移对 append-only 的行情表价值有限,反而增加心智负担。

## 决策

- 放弃 SQLAlchemy ORM,统一使用原生 SQL + 轻量封装(backend/db/)。
- 放弃 Alembic,schema 变更通过版本化的 SQL 脚本(migrations/*.sql)手工执行并记录。
- 保留 SQLAlchemy core 的可能性(仅作为连接池/方言适配),但当前不引入。

## 后果

正面:
- 代码更直观,SQL 可直接在 DBeaver 等工具中复用。
- 批量写入性能更可控。

负面:
- 失去自动迁移能力,需自行维护 migrations 目录和执行顺序。
- 跨库可移植性变差(目前仅支持 PostgreSQL)。

## 后续行动

- 建立 migrations/ 目录与 README,约定命名规范 `NNNN_description.sql`。
- 在部署文档中加入迁移执行步骤。
