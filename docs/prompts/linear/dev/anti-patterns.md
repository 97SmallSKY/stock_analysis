# Dev 子文档 · 反模式清单

> 父文档:[../dev.md](../dev.md)

---

## 🚫 Dev 特有的反模式(12+ 条)

| ❌ 错误做法 | ✅ 正确做法 | 对应规则 |
|---|---|---|
| 不转 In Progress 就开始写 | 开工第一件事改状态 | dev.md §2 Step 1 |
| 不自测就提 PR | pytest + vue-tsc + vitest + 冒烟,四层全过 | dev.md §2 Step 4 |
| commit 不带 `[STO-X]` 前缀 | 每个 commit 必须带前缀 | dev.md §2 Step 5 |
| PR 描述只写「修了个 bug」 | 必须按 dev/templates.md PR 模板填齐 | dev.md §2 Step 6 |
| 一个 PR 夹带多个不相关改动 | 拆多个 commit / 多个 PR,每个关联一个 issue | dev.md §3 单次 commit 守则 |
| 改了旧模块不读 PROGRESS | 改旧模块前 PROGRESS / CLAUDE / ADR 必读 | dev.md §3 |
| **v3**:在 new-branch 上直接改 | 必须独立分支 `<type>/STO-X-<slug>` | dev.md §2 |
| **v3**:用 `--force` 强推 | 用 `--force-with-lease` 保护他人 checkout | dev.md §4.2 |
| 切到 main 做改动 | 所有改动在 `new-branch` 派生分支,`main` 冻结 | README §3.6 |
| 从 main 往 new-branch merge | 禁止跨分支 merge/rebase/cherry-pick | README §3.6 |
| 直接在 jd 上改代码 | 本地改 → push → deploy,jd 只读 | dev.md §3 |
| 假定 API 字段名 = DB 列名 | 必查 `information_schema.columns` | dev.md §3 |
| 自己直接部署到 jd | 部署是 Ops 的事 | dev.md §1 |
| 覆盖率降找借口(「这段不好测」) | 补测 or 让 PM 建 follow-up waive,不默默放过 | dev.md §2 Step 4 |
| 自己把自己从 In Review 打回 In Progress | 让 Test 打回;我只能评论 @test 说明问题 | dev.md §1 |
| 用 `print()` 调试 | 用 logger(见 STO-12) | dev.md §3 |
| 硬编码密钥 / 绝对路径 | 用 `.env` 配置 | dev.md §3 |
| 引入 ORM | 禁用 ORM(ADR-0001),只用 psycopg2 | dev.md §3 |

## 🎯 Dev 的核心原则提醒

1. **三位一体 Git 关联** —— commit 前缀 + PR 标题 + PR 描述 Closes,缺一不可
2. **必建独立分支**(v3) —— 每个 issue 一个分支,永远不污染 new-branch
3. **自测 4 层不可省** —— pytest / vue-tsc / vitest / 本地冒烟
4. **Dev 不是 Test** —— 别代替 Test 打 test-passed,把这个权利留给 Test 角色
5. **改旧模块先读 PROGRESS** —— 基于记忆改是事故的常见根因
