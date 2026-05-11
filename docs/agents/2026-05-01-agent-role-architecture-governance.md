# 2026-05-01 Agent 角色与架构开发治理建议

> 审查范围: `.worktrees/sa-pm/CLAUDE.local.md`、`.worktrees/sa-dev/CLAUDE.local.md`、`.worktrees/sa-ops/CLAUDE.local.md`、`.worktrees/sa-audit/CLAUDE.local.md` 以及 `docs/agents/*`。
> 目标: 让 4-agent 流程不仅能"做完任务", 还要能稳扎稳打演进架构, 避免在既有代码上继续堆复杂度。

## 总体结论

当前 4 个角色已经覆盖 PM、开发、部署、审计, 但仍偏"工单流水线"。它能推动 issue 从 Backlog 到 Done, 却没有把"架构守门、重构节奏、代码边界、复杂度收敛"变成可执行的轻量机制。

**核心矛盾**: 原方案在"系统级根本限制"章节承认了 LLM 记不住、错误相关性高, 然后在治理方案里设计了更复杂的跨 agent 上下文传递。这个矛盾不解决, 治理层叠得越多, 实际运行脆弱性越大。

**新的原则**: 不是加更多流程, 而是让每一步的产出物足够简单、机器可读, 让 agent 不需要理解上下文就能执行下一步。脚本输出 → agent 读 stdout, 而不是 agent 读 agent 的自然语言 comment。

---

## AI 专属约束

这些规则优先级高于后文治理建议:

1. **PM 不做代码级全知判断。** PM 只定义业务边界、用户可见行为、数据口径和风险等级; 具体文件边界由 Dev 开工后用 `rg`、测试和局部阅读确认。PM 可以给"疑似路径", 不能把路径写成硬禁令。
2. **Auditor 不靠肉眼数行和找重复实现。** 行数、复杂度、lint 必须由脚本产出文本报告; Auditor 只读报告、判断趋势、建 issue。重复代码检测(M13)不使用 `jscpd`(对 Python 支持有限, 对跨语言语义重复无能为力), 改为人工标注 + 定期 review。
3. **保护测试不是复制 bug。** Characterization test 只保护"被 PM/AC 明确认定应保持"的行为。遇到疑似 bug 的旧行为, Dev 应记录为"待确认行为", 不写成硬断言。
4. **任何 agent 必须有放弃规则。** 同一状态节点连续 3 次失败, 或同一 issue 单轮处理超过 1 小时仍无明确进展, 必须 `blocked:need-human` 并停止继续消耗。
5. **小改动有快速通道。** 单文件、低风险、无契约变化的修复不强制写设计文档、不强制重构、不强制保护测试; 只要求最小验证和清晰交接。

---

## MCP/Tool 统一错误契约

> 写入 `docs/agents/common-rules.md` 末尾。四个 agent 的共享行为基线, 应先于角色卡修订完成。

所有外部调用(SSH、Playwright MCP、Git 命令、Linear MCP、curl)必须遵循统一的错误分类和处置规则。

### 错误三级分类

| 分类 | 判据 | 处置 |
| --- | --- | --- |
| **RETRYABLE** | 网络超时、SSH 连接重置、Linear API 限流/假空、`curl` 56/28 | 同一 tick 内重试 3 次 × 5s 间隔; 3 次全失败 → 本 tick 放弃, 下个 tick 重试; 连续 3 个 tick 仍失败 → 升级为 FATAL |
| **FATAL** | 凭证失效、仓库权限拒绝、SSH host key 变更、`deploy.sh` 返回非 0 | 立即 `blocked:need-human`, 不重试, 不继续当前 issue |
| **INCONSISTENT** | Linear state 与 git SHA 对不上、部署后 `git log` SHA ≠ 目标 SHA、同一 issue 两个 agent 看到不同 state | 停止当前 issue 流转; 加 `blocked:inconsistent` label(让 Ops/Dev 跳过此 issue); 建 `[AUDIT:INCONSISTENT]` issue 给 Auditor 判断严重度; **不**加 `blocked:need-human`(Auditor 先判断) |

**示例**:
- RETRYABLE: `curl: (28) Operation timed out`、`list_issue_labels` 返回 0 条
- FATAL: `git push` 被拒 `permission denied`、`deploy.sh exit 1`
- INCONSISTENT: `git merge-base --is-ancestor` 返回 false、部署后 `git log -1` 显示陌生 SHA

### 退避与熔断

- **同 tick 内重试**: RETRYABLE 错误在同一 tick 内做快速重试(3 次 × 5s), 不跨 tick 指数退避。原因: agent tick 间隔是分钟到小时级, 跨 tick 退避会导致流程卡数小时。
- **跨 tick 累计**: 同一 issue 同一操作连续 3 个 tick 都是 RETRYABLE → 升级为 FATAL, 加 `blocked:need-human`。
- **全局熔断**: 每个 agent 遇到 RETRYABLE 错误时, 在 issue comment 里追加结构化日志: `RETRYABLE: <原因> · issue=STO-<id> · tick=<timestamp>`。Auditor 扫 comment 里的 `RETRYABLE:` 前缀做频率统计。若同一类错误在 30 分钟内影响 ≥ 3 个 issue → 建 `[AUDIT]` severe 通知人工(可能是基础设施故障, 不是 transient error)。

**`blocked:inconsistent` 解除**: Auditor 处理完 `[AUDIT:INCONSISTENT]` issue 后, 移除该 label, Ops/Dev 下个 tick 才能继续处理。

---

## 角色重新分工建议

| 角色 | 当前定位 | 建议强化 | 不应承担 |
| --- | --- | --- | --- |
| PM | 产品 + 架构师 + 队列管理 | 明确为 Architecture Owner: 定业务边界、数据口径、风险等级、是否需要 ADR | 不直接写代码, 不臆造代码级路径边界, 不 commit 到仓库 |
| Dev | 实现 + 自测 + push | 明确为 Implementation Owner: 开工后用工具确认改动路径, 小步实现, 对 touched code 可维护性负责 | 不顺手大重构, 不绕过架构边界快速堆功能 |
| Ops | UI 验证 + 部署 + 数据修复 | 明确为 Runtime Owner: 验证线上行为、部署完整性、数据链路健康、artifact 清理 | 不承担产品口径和跨层架构改造 |
| Auditor | 监察 + 告警 | 明确为 Architecture Sentinel: 读取脚本产出的报告, 监控趋势和异常; 处理 INCONSISTENT 告警 | 不靠 LLM 肉眼数行、猜重复实现或做全局语义检索 |

---

## 是否需要新增"重构 agent"

### 短期建议: 不新增常驻角色, 先新增 Refactor Lane

Refactor Lane 由 PM 发起、Dev 执行、Auditor 审计、Ops 验证:

1. PM 建 `[重构]` issue, 类型为 `type:chore`, 必须写清"行为不变"还是"行为变化"。
2. Dev 只做该 issue 声明的模块边界内改造。**commit 分拆原则**: 尽量分 commit; 无法分开时在交接 comment 里说明原因, Ops 做好整体 revert 准备。
3. Auditor 对重构 issue 加严检查: 禁止混入功能需求, 禁止扩大范围。
4. Ops 对重构结果做回归验证, 即使无 UI 改动也要跑 API/smoke。

### 何时增加第 5 个 `sa-arch`

满足任一条件再新增:
- 近 7 天 `[架构审计]` issue > 5 个且 Dev 无法消化。
- 同一模块 3 次以上出现"修一个坏两个"的回归。
- 需要跨模块迁移。
- PM 的架构审计和队列管理互相挤占, 导致 Intake 堆积。

---

## 开发流程建议: 轻量 Architecture-first

### 1. 分级: 判断是否需要架构门槛

PM 在 T1 前增加分级:

| 级别 | 判定 | 要求 |
| --- | --- | --- |
| S | 单文件小修 / 文案 / 测试补充 / 明确 bug 一行修 | 直接进入 Dev, 不强制设计文档和保护测试 |
| M | 跨 2-4 个文件 / API 字段 / 前端展示口径 | PM 写业务影响; Dev 开工后补实际改动路径 |
| L | 跨层改动 / schema / 数据口径 / 路由与部署 | 需要短设计文档或 ADR 摘要, 不超过 60 行 |
| R | 纯重构 / 架构债务 | 进入 Refactor Lane, 默认行为不变; 若发现行为变化立即回 PM 决策 |
| H | 线上 P0/P1 hotfix | Fast Track: 先止血, 后补文档和重构 follow-up |

**注意**: 分级依据是"业务影响范围", 不是"改了多少个文件"(文件数由 Dev 开工后确认)。PM 的分级是初始估计, Dev 发现实际范围超出时必须回 PM 重新评估。

### 2. PM 只写业务影响, 不写架构影响段

PM 只写业务级描述(删除原来的"架构影响"段):

```text
## 业务影响
- 用户可见行为: 变化 / 不变化
- 数据/单位/口径: 变化 / 不变化
- 风险等级: S / M / L / R / H
```

Dev 开工后自己用工具确认改动路径, 不依赖 PM 的"疑似路径"。若发现风险超过 PM 判断, 进入 `@PM 需决策`。

### 3. Dev 开工前检查 + 客观越界检测

Dev Step 2 增加"开工前架构检查":

1. `rg` 找现有实现和相邻 helper。
2. 读对应 API/type/schema/test。
3. 判断是否已有工具函数可复用。
4. 若发现必须越界, 先 `@PM 需决策`, 不自行扩大范围。

**客观越界检测**(Dev 在 Step 2 执行, 不依赖主观判断):

```bash
# 统计触及的架构层(排除 docs/ 和 tests/)
CHANGED=$(git diff --name-only HEAD origin/new-branch | grep -v '^docs/' | grep -v '^tests/')
echo "$CHANGED" | grep -q 'backend/api/'    && echo "  → api 层"
echo "$CHANGED" | grep -q 'backend/domain/' && echo "  → domain 层"
echo "$CHANGED" | grep -q 'db/init/'        && echo "  → db schema 层"
echo "$CHANGED" | grep -q 'frontend/src/'   && echo "  → frontend 层"

# 文件数(排除 docs/ 和 tests/)
FILE_COUNT=$(echo "$CHANGED" | grep -c .)
```

触发越界回 PM 的条件(满足任一):
- 触及 ≥ 2 个**后端**层(`backend/api/`、`backend/domain/`、`db/init/` 任意两个)
- 非文档/测试文件数 > 4

触发时 Dev 在 issue 下 comment: `@PM 越界检测: 触及 <列举层>，原分级 <X>，建议升至 <Y>，等待决策。` 然后停止开发直到 PM 回复。

不建专用脚本——用 Dev 已有的 `git diff + grep` 完成, 减少维护负担。

### 4. 先补安全网, 再改实现

推荐顺序: "安全网 -> 小步实现 -> 契约同步", 按任务风险裁剪:

- S 级小修: 可直接改实现, 跑相关测试。
- M/L/R 级: 优先补最小安全网(单测、接口快照、Playwright 路径或手工 curl 断言)。
- Characterization test 只保护被 PM/AC 明确认定应保持的行为; 疑似 bug 的旧行为写 `TODO/待确认`, 不写成硬断言。
- Mechanical refactor 和 Behavior change 尽量分 commit。

### 5. 每个 commit 只表达一种意图

```text
chore(STO-123): add characterization tests for lhb amount formatting
refactor(STO-123): centralize lhb amount formatter usage
fix(STO-123): render lhb wan amounts with fmtAmountWan
chore(STO-123): document amount unit contract
```

禁止: 一个 commit 同时改前端 UI、后端 SQL、部署脚本和文档。

### 6. `@Ops ready` 只写 commit SHA + 分层验证指令

删除原来的"架构: 边界/复用/债务"段落。`@Ops ready` comment 格式:

```text
改动: <一句话>
commit SHA: abc1234
验证:
  - [UI] Playwright 打开 /lhb 确认金额列显示正常
  - [数据] curl /api/lhb?limit=1 | jq '.data[0].buy_amount' 应在 [0.01, 999999] 范围内
```

**分层验证指令规则**:

| 改动类型 | 验证方式 | 谁提供判据 |
| --- | --- | --- |
| UI 布局/交互(无数据口径变化) | Playwright 截图 + 用户复查 | Ops 截图, 用户看 |
| 数据口径/计算公式变更 | 可机器判定的断言(API schema check / 数值范围 / Playwright expect) | Dev 在 `@Ops ready` 里写 |
| 纯基础设施/部署配置 | smoke + healthcheck | 已有脚本 |

**关键约束**: 数据口径变更必须带自动化断言。Dev 不能只写"打开页面看金额", 必须写可机器判定的断言。

若 PM 的 AC 业务口径找不到可自动化的验证方式, Dev 在 `@Ops ready` 里标注 `验证: 需人工复查 <具体原因>`。Ops 在 T3.6 标 SKIP + `needs:human-decision`, 不自动 Done。

**"无法自动化"的判断前移**: PM 在写 AC 时, 对数据口径类改动必须预先说明"可自动化"还是"需人工", 不在 Dev 移交时再判断。

### 7. Auditor 只读脚本报告

**删除 M12-M16, 只保留 M1-M11 + M14**:

- M12(跨层漂移)、M13(重复实现)、M15(设计缺失)、M16(重构混功能): 这些依赖语义判断或跨语言分析, 脚本做不到, LLM 也不可靠。改为人工定期 review。
- M14(文件增长): 可自动化。检测方式: `git diff --stat` + `wc -l` 脚本产出报告。

**Auditor 审计报告新增**:
- 标注 `from_sha` 和 `to_sha`, 让两份报告可以做 diff。
- 扫描 issue comment 里的 `RETRYABLE:` 前缀做频率统计(全局熔断检测)。
- 检测 `blocked:inconsistent` label 的 issue, 判断严重度并决定是否升级为 `blocked:need-human`。

---

## Fast Track 与 Bail-out

### Hotfix 快速通道

`priority:urgent + type:bug` 满足线上 P0/P1 时启用:

1. PM 只写最小 AC: 现象、止血目标、回滚方式。
2. Dev 可跳过设计文档和保护测试, 但必须写清验证命令。
3. Ops 部署后必须做 post-deploy smoke 和关键路由验证。
4. Done 后自动建 follow-up: 补测试、补文档、清理临时实现。

### Agent 放弃规则

任何 agent 在同一 issue 同一状态节点满足任一条件, 必须停止继续尝试:

- 连续 3 次同类失败。
- 单轮处理超过 1 小时仍无明确进展。
- 测试/类型错误数量扩大而非收敛。
- 为修一个错误开始修改无关模块。

动作: 加 `blocked:need-human`, comment 写失败摘要、已尝试动作、下一步建议, 然后退出该 issue 的循环。

---

## 角色文件的具体修订建议

### PM 角色卡

建议增加:
- T1 前的 S/M/L/R/H 分级(依据业务影响范围, 不依据文件数)。
- `[重构]` issue 模板。
- M/L/R issue 只必填"业务影响"段, 预先说明"可自动化"还是"需人工"。
- 债务优先级决策规则: 同一模块连续 2 次回归 → 下一个 issue 必须是重构, 不接新功能。

建议删除:
- "架构影响"段模板(涉及层、疑似路径、回滚方式)。
- 每日限额 `[架构审计]` issue——如果没有重构消化机制, 只发现问题会让 Auditor 变成纯告警机器。

### Dev 角色卡

建议增加:
- 开工前架构检查 + 客观越界检测(git diff + grep, 见上)。
- "最小安全网 -> 小步实现 -> 契约同步"的顺序。
- 前端变更优先跑相关 vitest/typecheck/build。
- 同一节点 3 次失败或 1 小时无进展必须 bail out。
- `@Ops ready` 必须包含 commit SHA + 分层验证指令。
- 移 `In Verification` 后禁止在同一 issue 追加 push 新 commit。
- 发现需要重构时, 只能创建 `[重构]` follow-up, 不在功能 issue 里顺手做。

建议删除:
- "在本地记录将修改的文件清单"——跨 agent 上下文传递。
- "`@Ops ready` 增加架构: 段"——Ops 只需要 SHA 和验证指令。

### Ops 角色卡

建议增加:
- 部署前: `git merge-base --is-ancestor <SHA> origin/new-branch` 验证 SHA 在远端。
- 部署: `deploy.sh --sha <SHA>` 精确部署(不部署分支最新 HEAD)。
- 部署后: `ssh jd "git -C ~/stock_analysis log -1 --format=%H"` 确认版本一致, 再做 Playwright 验证。
- smoke 覆盖 L1(API 存活)和 L2(前端资源完整性, `smoke_chunks.py`)。
- data-bug 完成后必须跑 API/data smoke。
- UI 验证 artifact 路径改用 `{issue_id}/{commit_sha_short}/`。
- 遇到 INCONSISTENT 错误: 加 `blocked:inconsistent` label + 建 `[AUDIT:INCONSISTENT]` issue, 不自行继续。

建议删除:
- 读 Dev 的"架构: 段"——Ops 不需要理解 Dev 的自然语言描述。
- T3.6 在未部署目标 commit 的 jd 上验证(验证的是旧版本)。

### Auditor 角色卡

建议增加:
- M14(文件增长)指标: `git diff --stat` + `wc -l` 脚本产出报告。
- 审计报告标注 `from_sha` 和 `to_sha`。
- 扫 comment 里的 `RETRYABLE:` 前缀, 30 分钟内 ≥ 3 个 issue 触发 `[AUDIT]` severe。
- 处理 `blocked:inconsistent` label 的 issue: 判断严重度, 决定是否升级为 `blocked:need-human`, 处理完后移除 label。
- 时间模型监控: `In Verification` 平均等待时长 > 1h 则告警。

建议删除:
- M12-M13/M15-M16 架构指标——依赖语义判断, 改为人工定期 review。
- "每日架构趋势摘要"——跨 agent 上下文传递。
- "对 `@Ops ready` 的架构: 段做检查"——已删除该段落。

---

## 前置依赖: 必须在 Kickoff 前完成(人类执行)

以下基础设施必须在 4-agent 系统正式启动前完成。**脚本不存在则 agent 第一个 tick 就 bail out**。

| # | 任务 | 说明 | 状态 |
| --- | --- | --- | --- |
| 1 | 修复 `deploy.sh` | 支持 `--sha <sha>` 精确部署（`reset --hard` 避免 detached HEAD）; healthcheck 轮询等待服务就绪（最多 60s） | ✅ 已完成 |
| 2 | 扩展 `smoke_http.sh` (L1) | 现有脚本已是 API 存活检查，职责 L1 不变 | ✅ 已有，无需改动 |
| 3 | 新增 `scripts/smoke_chunks.py` (L2) | Playwright `page.on('response')` 检测懒加载 chunk 404；**本机执行**，不在 jd 上跑，本机需安装 `playwright + chromium` | ✅ 已创建 |
| 4 | 新增 `scripts/check_file_growth.sh` (M14) | `git diff` 统计新增行数 + `wc -l` 总行数，输出 STATUS: PASS/WARN，供 Auditor 读取 | ✅ 已创建 |
| 5 | `scripts/agent_status.sh` | 现有脚本通过 Linear GraphQL API 扫 `@Role tick` comment 判断存活，与 heartbeat 文件方案功能等价 | ✅ 已有（实现方式不同但功能等价）|
| 6 | 新增 `scripts/cleanup_artifacts.sh` | 删除 7 天前的 PASS artifact 和 30 天前的 FAIL/UNKNOWN artifact，支持 `--dry-run` | ✅ 已创建 |

---

## 推荐新增文档

1. `docs/agents/refactor-lane.md` — `[重构]` issue 怎么建、怎么验收、怎么禁止混入功能。
2. `docs/agents/architecture-gates.md` — S/M/L/R/H 分级、业务影响段、Fast Track。
3. `docs/agents/bail-out-rules.md` — 3 次失败 / 1 小时无进展 / 错误扩散时如何停止。

---

## 系统级根本限制(治理无法解决)

### 1. 验证闭环的错误相关性问题

多个 agent 共享同一底层模型的训练偏置, 倾向于犯同一类错误。错误相关性比人类团队更高。

**缓解方向**:
- Ops 验证时不读 AC, 只从用户视角操作页面(但对数据口径变更, Dev 必须提供可机器判定的断言)。
- Auditor 用静态分析脚本(非 LLM)做交叉检查。
- 黄金数据集: 金融系统应保留人类专家手工计算的历史 PnL 结果, 每次 agent 改完代码后跑回归比对。这是业务测试要求, 应进 CLAUDE.md 测试章节, 不在本治理文档范围内。

### 2. 时间模型的不确定性

4 个 agent 混用时间驱动(定时 ScheduleWakeup)和事件驱动(Linear state 变化)。同一个 issue 进入 `In Verification` 后, 被 Ops 处理的延迟在 0 秒到数小时之间不确定, 无法给出 SLA。Auditor M10 的">2h 告警"阈值和 Auditor 自身唤醒间隔可能处于同一数量级。

**缓解方向**: 要么全部改为事件驱动(Linear 轮询间隔 < 5 分钟), 要么全部改为时间驱动(所有 agent 同步唤醒 + 统一 epoch)。当前混合模式是最坏情况。

### 3. Linear state 与代码库状态异步

Linear state 不反映 git 状态。唯一可靠的绑定点: Dev 的 `@Ops ready` comment 里的 commit SHA。所有后续步骤以 SHA 为锚点, 不以 Linear state 为锚点。

### 4. LLM 长周期状态保持能力不足

**这是当前治理方案最根本的矛盾**: 承认了"记不住", 然后设计了更复杂的跨 agent 上下文传递。

**脚本适合做的**: 一致性校验(state vs commit SHA、label name vs Linear 实际)。
**脚本不适合做的**: 语义判断(这个重构是否越界、这个验证是否正确)。

减少 agent 数量可以降低状态保持失败的频率, 但不能消除根本限制——这是缓解手段, 不是根治方案。
