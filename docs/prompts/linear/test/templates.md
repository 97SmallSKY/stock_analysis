# Test 子文档 · 模板集

> 父文档:[../test.md](../test.md)

---

## 🧪 测试用例评论模板(Step 2 必写)

```markdown
🧪 **Test 用例(STO-X)** · 分支 `<type>/STO-X-<slug>`

### 0. Checkout(Test 必做第一步)
- [ ] `git fetch && git checkout <type>/STO-X-<slug>`(不在 new-branch 测)
- [ ] `git log -1 --oneline` 记录测试的 commit SHA

### 1. 单元测试(pytest) — Test 必跑
- [ ] happy path: `test_xxx_ok`
- [ ] 边界:空数据 / 单行 / 重复 / null / 超大
- [ ] 错误路径:`test_xxx_raises_value_error`

### 2. 前端(vue-tsc + vitest) — Test 必跑
- [ ] 类型检查:`vue-tsc --noEmit` 0 error
- [ ] 组件测试:vitest 覆盖新增 component

### 3. 页面测试(用户真实点击) — Test 必跑
- [ ] 本地起 dev server(`npm run dev` / `uvicorn`)
- [ ] 打开页面 `http://localhost:PORT/path`
- [ ] 点击 XX 按钮,观察 YY 响应
- [ ] 输入非法值,观察错误提示是否友好
- [ ] 在小屏/大屏各测一次(可选)

### 4. smoke 端点 — ⚠️ Test 不测,交 Ops
- [ ] PR 描述「Ops 部署注意」章节完整即可

### 5. 服务器部署 — ⚠️ Test 不测,交 Ops

### 特殊情况 · 不适用项
- [ ] 项目 X 不适用(理由:本 PR 纯后端,无前端改动)
```

---

## ✅ 验收通过签字评论(打 test-passed 时)

```markdown
✅ **Test 签字 · test-passed**

- pytest: 729 passed, coverage XX.XX%(+0.01% vs main)
- vue-tsc: 0 error · vitest: 43 passed
- 页面测试:按 §3.10 用例勾选完毕(见上方 🧪 测试用例评论)
- 测试分支:`<type>/STO-X-<slug>` @ commit `<SHA>`
- 不适用项:smoke 端点 + 服务器部署(交 @ops)

@ops 请部署 → smoke 通过后转 Done(三方共识完成)
```

---

## 🟡 验收打回评论(In Review → In Progress)

```markdown
🟡 **Test 打回 · Request changes**

### Blocker(必改,引用 DoD 条目)
1. DoD #1(pytest 不过):`test_xxx` 失败,错误:<...>
2. DoD #3(页面测试):点击 XX 按钮无响应,预期 YY
3. <...>

### 建议(非阻塞)
- <...>

@dev 请修复后重新提 PR。我会重新验收。
```

---

## 🔄 文档不合格评论(不改状态)

```markdown
📝 **Test 需 Dev 补 PR 描述**(不打回状态)

按 test.md §2 Step 0 上游期望,PR 描述缺失:
- [ ] 「自测结果」4 层勾选
- [ ] 「issue 验收项勾选情况」
- [ ] 「Ops 部署注意」

@dev 请补齐 PR 描述,无需修代码。补完 @我。
状态保持 In Review,@test 保留。
```

---

## 📋 补测 issue 模板(闭环 A · 主动建)

```markdown
## 现状

<模块名> 当前覆盖率 X%,低于目标 80%。
`coverage report` 显示缺失:
- backend/.../xxx.py:30-45(错误路径)
- backend/.../xxx.py:78-92(边界)

## 任务范围

- [ ] 覆盖 `function_a` 空数据 / 单行 / 重复
- [ ] 覆盖 `function_b` 错误路径(raise ValueError)
- [ ] 目标:模块覆盖率 ≥ 80%

## 验收

- 新增测试文件 ≥ 1 个
- 所有用例绿
- `pytest --cov=<module>` 输出 ≥ 80%
```

---

## 🔁 回归测试 issue 模板(bug 修复后补)

```markdown
## 背景

修复了 STO-X 的 bug:<一句话>。
必须加回归测试,防止未来再次出现。

## 任务范围

- [ ] 加一个能**在修复前失败、修复后通过**的 case
- [ ] 覆盖这个 bug 的**根本触发条件**(而不是表象)

## 验收

- 回退修复 commit,新测试应失败
- 恢复修复 commit,新测试应通过
```
