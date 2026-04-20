# Dev 子文档 · Commit / PR / 评论模板

> 父文档:[../dev.md](../dev.md)

---

## 📝 Commit Message 模板

```
[STO-X] <type>: <动宾短句,50 字以内>

<可选详情,每行 72 字以内>

<可选 footer,如 Refs STO-Y / Breaks API>
```

`<type>` 取自:`feat` / `fix` / `refactor` / `test` / `docs` / `chore`

**示例**:
```
[STO-6] feat: add api-key header auth middleware

- 在 backend/api/deps.py 新增 verify_api_key dependency
- 写端点(robots/backtest)强制,读端点按需
- .env.example 补 API_KEY 配置
```

---

## 🔀 PR 标题模板

```
[STO-X] <完整描述,清晰概括本 PR>
```

---

## 📋 PR 描述模板(v3 · 必填)

```markdown
Closes STO-X

## 改动概要
- ...

## 分支
- base: `new-branch`
- head: `<type>/STO-X-<slug>`

## 自测结果(Dev 自签)
- [ ] pytest -q 全绿,覆盖率 XX.XX%(基线 YY.YY%)
- [ ] vue-tsc --noEmit 0 error
- [ ] vitest run 全绿
- [ ] 本地冒烟关键端点 200

## issue 验收项勾选
- [x] 验收项 1
- [x] 验收项 2

## 影响范围
- 后端:...
- 前端:...
- DB schema:...(有变更务必附 migration)

## Test 验收建议(@test)
- 重点看:...
- 页面测试建议路径:<URL or 页面名>

## Ops 部署注意(@ops)
- 部署前需要:<如无则写「无」>
- 重启 scheduler / nginx:<是/否>
- DB migration:<是/否 + 命令>
- FF-only merge 预期:分支基于 <datetime> 的 new-branch,若冲突愿意 rebase
```

---

## 📬 Linear 交接评论模板

### 提 PR 后的交接评论

```markdown
✅ **Dev 已提 PR** — 交接 @test 和 @ops

- PR: <URL>
- 分支:`<type>/STO-X-<slug>` @ commit `<SHA>`
- 自测:pytest ✅ / vue-tsc ✅ / vitest ✅ / 本地冒烟 ✅
- 覆盖率:XX.XX%(基线 YY.YY%)

@test 请按 test.md §3.10 5 项测试 + 打 test-passed 标签
@ops 待 test-passed 后 JIT merge 部署
```

### 退回 PM 的评论

```markdown
🔙 **Dev 退回 PM(issue 不合规)**

按 dev.md §2 上游期望,本 issue 缺失:
- [ ] <缺失项 1,例:验收标准不可量化>
- [ ] <缺失项 2,例:任务范围不是 checklist>

@pm 请补齐后 @我重新开工。
```

### rebase 后的评论

```markdown
🔄 **Dev 已 rebase 到最新 new-branch**

- 旧 head:`<old-SHA>`
- 新 head:`<new-SHA>`
- 无冲突 / 已解决冲突:<说明>

@ops 可重试 JIT merge
```
