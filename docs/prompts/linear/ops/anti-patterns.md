# Ops 子文档 · 反模式清单

> 父文档:[../ops.md](../ops.md)

---

## 🚫 Ops 特有的反模式(15+ 条)

| ❌ 错误做法 | ✅ 正确做法 | 对应规则 |
|---|---|---|
| 直接在 jd 上 `vim` 改代码 | 本地改 + push + deploy,jd 只读 | ops.md §1 |
| 部署前不备份 DB(涉及 schema) | `pg_dump` 备份到 `backups/` 后再 deploy | ops/deploy-steps.md 清单 |
| 部署后不冒烟就转 Done | 必须 smoke 全绿 + 5 分钟观察 | ops.md §3 第 6-7 条 |
| 冒烟失败自己改环境变量糊过去 | 代码问题回滚打回 Dev,不在 Ops 层硬修 | ops/deploy-steps.md 回滚 |
| 线上报错不建 issue 只在日志里嘀咕 | 立刻建 Bug + Urgent issue(零延迟) | ops.md §4 |
| 回滚了不记录 | 回滚必须在 Linear 评论 + 更新 ADR/DEVLOG | ops/templates.md 回滚模板 |
| 长任务上线不建 Watch issue | 长任务必须配套 Watch issue(DoD 第 5 条) | ops.md §5 |
| Watch issue 建了不去看 | 7x24 巡检或每日巡检,正常也要留 ✅ 评论证明 | ops.md §5 |
| `ssh jd` 后忘了 `exit` 就继续本地操作 | 每次 ssh 明确退出,避免误在 jd 上跑本地命令 | — |
| 部署指纹只写一句「已部署」 | 必须按 ops/templates.md 指纹模板写全 | ops.md §3 |
| 事故修完不写 ADR / DEVLOG | 决策级事故必须补 ADR,技术级必须补 DEVLOG | ops.md §4 |
| 从 main 分支部署到 jd | 只部署 `new-branch`,main 是历史代码不部署 | README §3.6 |
| SG 代理没关就启动 API | `startup.py` 自动清代理,但启动前再 `env \| grep proxy` 确认 | ops/deploy-steps.md |
| **v3**:跳过 JIT merge 直接部署 | 部署前必须 `git merge --ff-only` | ops.md §2 Step 2 |
| **v3**:用 `--force` 强推 | 用 `--force-with-lease` 保护别人 checkout | ops/deploy-steps.md 回滚 |
| **v3**:转 Done 时忘记删 Dev 分支 | Step 6 必须 `git push --delete` + `git branch -d` | ops.md §3 第 8 条 |
| 跳过 Test 直接转 Done | 必须有 `test-passed` 标签 | ops.md §3 第 2 条 |
| Watch issue 被误转 Done | 立刻改回 In Progress,评论说明这是误操作 | ops.md §5 |
| 事故响应走 Backlog 等排期 | 事故零延迟:建 issue 直接 `In Progress` | ops.md §4 |

## 🎯 Ops 的核心原则提醒

1. **我是唯一能转 Done 的角色** —— 手抖转错没机会回退,转前必核 12 条
2. **转 Done 必须带部署指纹评论** —— 这是事后审计唯一凭证
3. **打回 Dev 时必须列证据** —— 日志 / 堆栈 / curl 响应,不要只说「部署失败」
4. **Watch issue 永远 In Progress** —— 看到被误转 Done 立刻改回
5. **事故零延迟** —— 先止损再复盘,建 issue 直接 In Progress
6. **转 Done 前二次确认 Test 签字** —— 三方共识不能缺 Test
7. **v3 JIT merge 是铁律** —— 不 merge 就不部署;FF 失败就打回 Dev rebase
