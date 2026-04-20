# Dev 子文档 · Code Review 清单

> 父文档:[../dev.md](../dev.md)

---

## 🔍 Review 别人 PR 的 16 项清单

审别人 PR 时,按以下 3 大类 16 项逐一核对。**有一条命中** = **Request changes**。

### 基础 6 项(文档 / 关联)

- [ ] Commit message 带 `[STO-X]` 前缀?
- [ ] PR 标题带 STO-X?
- [ ] PR 描述含 `Closes STO-X`?
- [ ] PR 描述填了模板(含分支信息)?
- [ ] issue 任务范围 checklist 打勾了?
- [ ] 自测 4 层都过了(pytest / vue-tsc / vitest / 冒烟)?

### 代码质量 6 项

- [ ] 新增代码有对应单测?
- [ ] 覆盖率不降?
- [ ] 无 `print()` / `console.log` 残留?
- [ ] 无硬编码密钥 / 绝对路径?
- [ ] 变量命名清晰(无 `a` / `b` / `tmp`)?
- [ ] 复杂逻辑有注释或拆小函数?

### 架构合规 4 项

- [ ] 不违反 ADR(特别是 ADR-0001 禁 ORM)?
- [ ] 基于 `new-branch` 派生,没从 `main` merge?
- [ ] schema 变更附 migration?
- [ ] API 契约变更更新了 OpenAPI 或前端 types?

---

## 📝 Review 结论格式

### ✅ Approve(无 blocker)

```markdown
## Approve ✅

基础 6 / 代码质量 6 / 架构合规 4 全过。

**建议(非阻塞)**:
- <可选,给 Dev 的改进建议>

转 @test 继续。
```

### 🟡 Request changes(有 blocker)

```markdown
## Request changes 🟡

### Blocker(必改)
1. <具体问题 + 对应清单条目,如『基础 6.3:PR 描述未含 Closes STO-X』>
2. <...>

### 建议(非阻塞,可以后做)
- <...>

改完再 @ 我重新审。
```

### ❌ Reject(罕见 · 方向性错误)

用于:方向完全错了 / 与 ADR 根本冲突 / 应该拆成多个 PR。建议关 PR 重开新的。
