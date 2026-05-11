# Agent 边缘场景手册

> 渐进披露入口。`transition-checklist.md` 引用这里的锚点;agent CLAUDE.local.md 不重复细节。
> 每场景 ≤ 10 行;新增场景请保持简洁,超了拆子文件。

## 索引

| # | 场景 | 触发频率 | 谁处理 |
|---|---|---|---|
| E1 | T4 部署成功但 smoke 失败（含 L1/L2 分层处置）| 中 | Ops |
| E2 | issue 二次 Done(rollback 修复后)label 幂等 | 中 | Ops |
| E3 | verify:pass / verified:human 撤销时机 | 低 | 用户 |
| E4 | P4 数据修复跳过 In Verification 的安全边界 | 中 | Ops |
| E5 | Dev Step 4b 部分成功(comment 发了 state 切换失败)| 低 | Dev |
| E6 | M6 commit 一致性应覆盖 In Verification | 持续 | Auditor |
| E7 | type:watch 子 issue 流转模式 | 低 | Ops/PM |
| E8 | 多 issue 同 commit | 低 | Dev |
| E9 | PM bootstrap label 创建失败 | 一次性 | PM |
| E10 | umbrella type:data-bug | 罕见 | Ops |
| E11 | needs:human-decision 残留(用户回复后没人摘)| 持续 | PM |
| E12 | Ops tick 心跳缺 verify:pass 自检字段 | 持续 | Ops |
| E13 | Agent bail-out: 3 次失败 / 1 小时无进展 | 持续 | 全部 |
| E14 | INCONSISTENT 错误：SHA 不在远端或部署后 SHA 不匹配 | 低 | Ops + Auditor |
| E15 | smoke_chunks.py 失败（L2 前端资源）vs smoke_http.sh 失败（L1 API）| 中 | Ops |
| E16 | Dev 越界 needs-regrade（回 PM 重新分级）| 中 | Dev + PM |
| E17 | blocked:inconsistent label 生命周期 | 低 | Auditor |

---

## E1 · 部署成功但 smoke / healthcheck 失败（含 L1/L2 分层处置）

**L1（smoke_http.sh API 存活）失败**：
1. 不打 `verify:pass`，**不挪 Done**
2. `state='In Progress'`（回 Dev），D 型 comment：`BLOCKED: L1 smoke 失败 · API 不可达 · <错误>`
3. 不自动 rollback（等用户决定）

**L2（smoke_chunks.py 前端资源）失败**：
1. 不打 `verify:pass`，加 `blocked:need-human`，state 留 In Review
2. D 型 comment：`BLOCKED: L2 smoke 失败 · 前端 chunk 404 · <资源路径>`
3. **Why**：L2 失败通常是 deploy.sh 没完整重建 dist/，需人工判断是 hotfix 还是重新部署

**healthcheck 非 200**：同旧逻辑，加 `blocked:need-human`，不 rollback，等用户拍板。

## E2 · issue 二次 Done · label 幂等

**场景**:issue 第一次 Done + verify:pass → 线上发现 bug → Ops 移回 In Progress → Dev 修 → Ops 二次部署。

**规则**:`save_issue(labels=[...existing, 'verify:pass'])` 时 existing 里若已有此 label,**直接传不报错**(Linear API 幂等)。无需先去掉再加。

**Auditor M11 不告警**:这种 issue Done 时一直有 verify:pass,符合规则。

## E3 · verify:pass / verified:human 撤销

**verify:pass**:Auditor / Ops 永不撤。只有用户在 Linear UI 手动撤(表示"事后发现机器误验证")。

**verified:human**:同上,只用户手动撤。撤后 Auditor 看到的是 Done + verify:pass(M11 不告警),正常。

**反模式**:agent 任何角色因为"看起来不对"擅自 `save_issue(labels=[...without 'verify:pass'])` —— 立即触发 [AUDIT] 严重告警。

## E4 · P4 数据修复的路径硬约束

Ops 在 P4 路径白名单内修复后,**只允许打 verify:pass + Done 跳 T3.6**,前提是改动**完全在白名单内**:

```
backend/data_gateway/** · backend/ingest/** · backend/domain/lhb/{parser,ingestor,aggregator}.py · db/init/NN_*.sql
```

**任一改动越界**(包括 `backend/api/**` / `frontend/**` / 跨域 backend):
- 立即停手,不 Done
- T5 升级 `BLOCKED: 数据 bug 改动越界,需走 Dev T3.6 验证`
- assignee 不变(还是 Dev user id),但 PM 下轮看到 → 转 Dev 流程

**Why**:数据路径改动无 UI 影响才能 SKIP UI 验证;若顺手改了 API/前端,必须 Playwright MCP 验证,Ops 自己跳过等于盲签。

## E5 · Dev Step 4b 部分成功

**场景**:4a comment 已发,4b `save_issue(state='In Verification')` API 报错。

**判定下轮重试条件**(精确到避免误判):
1. issue 最新 comment 是 `@Ops ready` 且其后无 Ops comment
2. **且** `get_issue(id).state == 'In Progress'`

两条同时满足 → 重试 4b 一次。否则视为已流转,不动。

**连续 3 次 4b 失败**:T5 `BLOCKED: Linear API 切 In Verification 失败`。

## E6 · M6 commit 一致性应覆盖 In Verification

**Auditor 扫描范围**:state ∈ {`In Verification`, `In Review`, `Done` 近 24h} 的 issue,从最新 `@Ops ready` comment 提取 `commit=<sha>`,验证 `git cat-file -e <sha>` + 是 `origin/new-branch` 的祖先。

**任一不一致 → severe**(部署/验证会因找不到 commit 而失败)。

## E7 · type:watch 子 issue 流转

Ops 巡检发现真故障 → 建 `[数据巡检] xxx` 子 issue:

- **代码改动型**(需要修代码) → 普通 `type:data-bug` 流程,T4 完成时打 `verify:pass`
- **误报关单型**(无改动,确认是数据源抖动) → `type:chore` + `state='Canceled'` + comment 说明,**不打 verify:pass**

禁:把误报关单当 Done(M11 会告警 Done 缺 verify:pass,语义错位)。

## E8 · 多 issue 同 commit

**场景**:Dev 一个 commit 顺手解决 2 个 issue。

**强约束**:1 issue 1 commit。顺手修复另一 issue 的 bug,**必须新建 commit 引用第二个 STO-id**(`fix(STO-B): xxx`),即便 diff 重叠 0 行也要拆 commit。

**违反检测**:Auditor 扫近 24h commit message,同一 sha 出现在 ≥ 2 个 issue 的 `@Ops ready` comment → minor 告警提示拆分。

## E9 · PM bootstrap `verify:pass` label 创建失败

**场景**:`create_issue_label('verify:pass')` 报错(已存在 / API 限流 / 颜色冲突)。

**处置**:
- 已存在 → `list_issue_labels` 拿现有 id,回填 state-machine.md,**视为成功**
- 限流 → 下轮重试(指数退避)
- 真创建失败 3 次 → `blocked:need-human` 在 sentinel issue

**Ops 兜底**:T4 打 label 时若发现 label 不存在,D 型 `BLOCKED: verify:pass label 缺失,请 PM 补创建`,issue 留 In Review 不挪 Done。

## E10 · umbrella type:data-bug

PM 给一个 umbrella `type:data-bug`(描述含多个表 / 多个修复点)→ Ops 处置:

- 主 session 解析子项,**N 次串行** P4 流程,每子项一独立 commit
- 每子项独立 pytest + push;全过后整个 umbrella 一起 `state='Done', verify:pass`
- 若某子项越白名单 → 整个 umbrella T5 blocked,不部分 Done

## E11 · needs:human-decision 残留

**问题**:PM 打了 label,用户回复"选 B"后,无人摘 label。

**PM 每 tick 扫**:
```python
for issue in list_issues(labels_contains='needs:human-decision'):
    last = list_comments(issueId=issue.id, limit=3, orderBy='createdAt-desc')
    if last[0].user_id != PM_USER_ID and last[0].createdAt > issue.updated_for_label:
        # 用户已回复,提示确认
        save_comment(issue.id, B型: '@user 已读您的决策<摘要>,确认后 PM 将摘除 needs:human-decision label')
```

**用户在该 comment 下回复"确认"或 PM 24h 后自动摘**(代决授权,memory `feedback_pm_autonomous_decide.md`)。

## E12 · Ops tick 心跳加 verify:pass 自检

A 型心跳模板扩展:
```
@Ops tick <ISO8601> · P2=<N>/<last:result> · P3=<N>/<last:result> · vp_打=<本tick打verify:pass次数> · next=<HH:MM>
```

`vp_打=0` 连续 ≥ 6 tick 而 P3 队列 > 0 → Auditor 抓到提示 Ops 部署链路不健康。


## E13 · Agent bail-out: 3 次失败 / 1 小时无进展

**触发**:任一 agent 在同一 issue 同一状态节点连续 3 次同类失败, 或单轮处理超过 1 小时仍无明确进展。

**处置**:
1. 立即停止继续尝试, 不再扩大改动范围。
2. 加 `blocked:need-human`。
3. D 型 comment 写:失败类型 / 已尝试动作 / 当前证据 / 推荐下一步。
4. state 保持当前, 等人工或 PM 重新分派。

**Why**:防止无人值守时陷入测试修复、类型修复、部署重试或状态流转的无限循环, 控制 token 和线上风险。

---

## E14 · INCONSISTENT 错误：SHA 不在远端或部署后 SHA 不匹配

**场景 A（T3.5，SHA 不在远端）**：`git merge-base --is-ancestor <sha> origin/new-branch` 返回非 0。
- Dev 的 push 可能失败但自己不知道（Linear state 已切但代码未到远端）
- Ops 处置：加 `blocked:inconsistent` label + 建 `[AUDIT:INCONSISTENT]` issue + **不**部署

**场景 B（T3.6，部署后 SHA 不匹配）**：`ssh jd "git log -1 --format=%H"` ≠ 目标 SHA。
- deploy.sh 成功但部署了错误的版本
- Ops 处置：同上，加 `blocked:inconsistent` + 建 `[AUDIT:INCONSISTENT]` issue

**Auditor 处置**：判断严重度；可接受则移除 `blocked:inconsistent`，让 Ops 下轮重试；不可接受则升级为 `blocked:need-human`。

---

## E15 · smoke_chunks.py（L2）失败 vs smoke_http.sh（L1）失败

| 类型 | 判定 | 处置 |
|---|---|---|
| L1 失败 | `smoke_http.sh` 非零 | state → In Progress，D 型 comment `@Dev L1 smoke 失败` |
| L2 失败 | `smoke_chunks.py` 非零（chunk 404）| 加 `blocked:need-human`，state 留 In Review，等人工判断 |
| L2 脚本不存在 | `smoke_chunks.py` 文件缺失 | 打 `blocked:need-human`，D 型 comment `BLOCKED: smoke_chunks.py 不存在，前置依赖未完成` |

**Why**：L2 失败根因通常是 deploy.sh 没完整重建 dist/，Dev 改代码解决不了，需要人工介入修脚本或重新部署。

---

## E16 · Dev 越界 needs-regrade（回 PM 重新分级）

**触发**：Dev 开工前越界检测（见 sa-dev-CLAUDE.local.md Step 2）发现：
- 触及 ≥ 2 个后端层（`backend/api/`、`backend/domain/`、`db/init/` 任意两个），或
- 非文档/测试文件数 > 4

**处置（Dev）**：
1. 在 issue 下 comment：`@PM 越界检测: 触及 <列举层>，原分级 <X>，建议升至 <Y>，等待决策`
2. **停止开发**，state 留 In Progress，不移 Ready to Verify
3. 等 PM 回复决策（升级分级 / 拆子 issue / 确认在范围内继续）

**处置（PM）**：读 Dev comment，决策后回复。若确认越界：拆 issue 或建 `[重构]` follow-up。

---

## E17 · blocked:inconsistent label 生命周期

**打标**：Ops 遇到 INCONSISTENT 错误时自动打（见 E14）。

**解除**：Auditor 处理完对应 `[AUDIT:INCONSISTENT]` issue 后移除。

**期间**：
- Ops/Dev 扫队列时**过滤掉**带此 label 的 issue，不处理
- Auditor 在 Step 4.5 存疑扫描中优先处理
- 超过 24h 未处理 → Auditor 建 `[AUDIT] severe` 告警

**PM bootstrap 确认**：此 label 在 12 个标准 label 列表中（state-machine.md），bootstrap 时自动创建。

---

## 维护

新增场景按严重度排,索引表第一列编号递增。删场景前先 grep `transition-checklist.md` 和 `sa-*/CLAUDE.local.md` 确认无引用。
