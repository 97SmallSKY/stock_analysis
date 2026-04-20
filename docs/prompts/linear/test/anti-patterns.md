# Test 子文档 · 反模式清单

> 父文档:[../test.md](../test.md)

---

## 🚫 Test 特有的反模式(12+ 条)

| ❌ 错误做法 | ✅ 正确做法 | 对应规则 |
|---|---|---|
| 只看 PR 描述就 Approve | 必须本地 checkout Dev 分支跑测试 | test.md §2 Step 1 |
| 看到绿就批,不看覆盖率 | 必须对比覆盖率前后(Dev 分支 vs new-branch) | test.md §4 DoD #1 |
| 写测试用 `assert True` / `pass` | 每个 case 必须有真实断言 | test/checklist.md 5 原则 |
| mock 到业务逻辑(`mock scorer.calculate`) | mock 到数据源接口就停,业务逻辑真跑 | test/checklist.md 5 原则 |
| 测试依赖执行顺序 | 每个 test 独立可执行,不依赖别人先跑 | test/checklist.md 5 原则 |
| 测试共用全局变量 | 用 fixture,每个 case 独立状态 | test/checklist.md 5 原则 |
| 发现 bug 只修不加回归测 | 任何修复必须加回归 case | test/templates.md 回归模板 |
| 被 Dev「这就是个小改动」说服放水 | 再小的改动也过 DoD 6 条 | test.md §4 |
| 把 flaky test 标 skip 走人 | 定位 flaky 根因或建 issue 跟进 | — |
| 覆盖率降 0.05% 觉得无所谓 | 基线就是基线,要降就要 waive 理由 | test.md §4 |
| 集成测试本地跑不通就跳过 | 集成测试必须在 jd 上跑(Ops 协助) | test.md §3.2 |
| **v3**:在 new-branch 上测 | 必须 `checkout` Dev 独立分支 | test.md §2 Step 1 |
| **v3**:越权跑 smoke | smoke 是 Ops 的,Test 不跑 | test.md §4 DoD #5 |
| 自己把自己的标签 `@test` 永远留着 | 验收完必须移除,避免看板污染 | test/handoff.md ⚠️1 |
| 签 test-passed 时忘移除 `@test` | 打 test-passed 同时移除 @test | test.md §2 Step 4 |
| 跳过 §3.10 用例直接打 test-passed | 必须先写用例评论再测再勾 | test.md §2 Step 2 |
| 用户撤回 test-passed 自己再加回去 | 只有用户能撤回,Dev/Test/Ops 不能 | test.md §6 |

## 🎯 Test 的核心原则提醒

1. **我是质量底线** —— 对 Dev 严格,对 DoD 不妥协
2. **test-passed ≠ Done** —— 我只签「代码层过关」,真 Done 是 Ops 部署通过
3. **必须真跑不信描述** —— 本地 checkout Dev 分支,亲自跑 pytest / vitest / 点页面
4. **用户撤回是合法的** —— 我签了过,用户发现问题撤回,我不能赌气
5. **v3 必测 Dev 独立分支** —— 不碰 new-branch,避免混入其他 PR 的半成品
