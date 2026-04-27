# 无人值守 Agent · 启动手册（4 agent · 7×24 小时 · 2026-04-25）

> 本文件说明如何启动 **4 个** agent。每个在独立的 worktree + 独立的 Claude Code 会话中运行 `/loop` 自驱。
> 你（人）是唯一的进程管理器：开终端、跑 `claude`、发首条 /loop 指令、关终端 = 停 agent。

## 架构速览

```
stock_analysis/                       ← 主仓库（人工介入专用）
├── .worktrees/
│   ├── sa-pm/       CLAUDE.local.md  ← PM agent（/spec + /plan + Linear 管理 + 架构审计）
│   ├── sa-dev/      CLAUDE.local.md  ← Dev+Test agent（/build + /test，唯一写 new-branch 非数据路径）
│   ├── sa-ops/      CLAUDE.local.md  ← Ops agent（UI验证 + 部署 + smoke + 数据采集监控）
│   └── sa-audit/    CLAUDE.local.md  ← Auditor agent（M1-M10 监察 + 存疑 @你 + 被动验 Done）
└── docs/agents/
    ├── KICKOFF.md                    ← 本文件（你看的）
    ├── common-rules.md               ← 4 agent 共享表达铁律
    ├── transition-checklist.md       ← 状态转换权威门槛 T1-T6（含 T3.6 UI 验证）
    └── state-machine.md              ← PM 首轮产出，Linear states 映射
```

## 完整流转图（7×24 自动化）

```
你建 issue（打 auto:stock-analysis）
         ↓
      Intake（Backlog）
         ↓ sa-pm /loop：/spec → /plan → T1 补 AC
   Ready for Dev（Todo）
         ↓ sa-dev /loop：T2 开工
      In Progress
         ↓ sa-dev /loop：/build → /test → T3 push + @Ops ready（含验证: 段）
   Ready to Verify                    ← 2026-04-25 新增
         ↓ sa-ops /loop：T3.6 Playwright MCP 验证
         ├── PASS/SKIP → Ready to Deploy（In Review）
         └── FAIL → 回 In Progress，@Dev 修复
   Ready to Deploy（In Review）
         ↓ sa-ops /loop：T3.5 FF 托底 → T4 部署 → smoke_http.sh
        Done
```

## 10 个标准 Label（PM 首轮自动创建）

**核心 6 个**：

| Label | 颜色 | 作用 | 谁打 |
|---|---|---|---|
| `auto:stock-analysis` | 绿 | 项目开关，agent 只处理带此 label 的 | **人工** |
| `type:feature` | 蓝 | 新特性 → Dev 写 CHANGELOG | PM |
| `type:bug` | 红 | bug 修复 → Dev 写 CHANGELOG | PM |
| `type:chore` | 灰 | 重构/文档/基建 → Dev 写 DEVLOG | PM |
| `type:watch` | 紫 | 巡检类永久 In Progress，Auditor M1/M8 豁免 | Ops |
| `blocked:need-human` | 黄 | agent 卡死求救（火灾，立即处理）| agent 自动 |

**扩展 4 个（2026-04-25）**：

| Label | 颜色 | 作用 | 谁打 |
|---|---|---|---|
| `needs:human-decision` | 粉 | 正常流程等你拍板（不紧急，早上扫一遍）| PM（6 场景）|
| `domain:lhb` | 青 | 龙虎榜业务域 issue | PM |
| `verified:human` | 深绿 | **你**人工验证通过 | **人工** |
| `type:data-bug` | 橙 | 数据采集/质量 bug → 路由 sa-ops | PM |

## 你的日常 SOP（打开 Linear 3 件事）

1. 筛 `blocked:need-human` → 火灾优先，立即处理
2. 筛 `needs:human-decision` → 一句话决策 + 移除该 label
3. 筛 state=`Ready to Verify`（如果 Ops 异常时有积压）→ 手动检查

> **注意**：正常情况下你不需要操作 Ready to Verify——sa-ops 会自动 Playwright MCP 验证。只有 Ops 打了 `blocked:need-human` 时才需要你介入。

## 启动顺序（**严格按顺序，一个一个起**）

### ① PM 先启（bootstrap labels + state-machine）

```bash
cd /Users/xiaotian/Develop/personal/stock_analysis/.worktrees/sa-pm
claude
```

首条输入：
```
/loop 你是 PM agent。读 CLAUDE.local.md 和 ../../docs/agents/common-rules.md 和 ../../docs/agents/transition-checklist.md。执行首轮 bootstrap:(1) 创建 10 个标准 label 若缺失(6 核心 + 4 个 2026-04-25 新增: needs:human-decision / domain:lhb / verified:human / type:data-bug);(2) 确认或创建 Linear state「Ready to Verify」(如已有同义 state 则记录其真实名和 id);(3) 写 docs/agents/state-machine.md 映射 6 个语义槽(含 Ready to Verify);(4) 创 sentinel issue 贴 state-machine 内容让 Dev 代为提交。完成后进入稳态循环,拿到 Intake issue 先执行 /spec → /plan 再流转,按 CLAUDE.local.md §打 needs:human-decision 的 6 场景 主动打标。
```

等你在 Linear 看到 `[PM] bootstrap done` sentinel issue = PM 就绪。

### ② Dev 次之（接手提交 state-machine.md）

```bash
cd /Users/xiaotian/Develop/personal/stock_analysis/.worktrees/sa-dev
claude
```

首条输入：
```
/loop 你是 Dev+Test agent。读 CLAUDE.local.md 和 ../../docs/agents/common-rules.md 和 ../../docs/agents/transition-checklist.md。首轮先处理遗留:若 sentinel issue 里有 state-machine.md / self-probe.md 内容未 commit,先写文件 + commit + push(type:chore 语义)。然后进入稳态循环。开发时用 /build 逐 slice 实现,测试用 /test 自动修复(最多 3 次)。Step 4b 必须移到「Ready to Verify」state(不是 In Review),让 Ops 先跑 Playwright MCP 验证。验证: 段 URL 必须用 jd 地址(117.72.184.8:8000),不得写 localhost。忽略 type:data-bug label 的 issue(归 sa-ops)。
```

### ③ Ops 再启（bootstrap self-probe + Playwright MCP 验证）

```bash
cd /Users/xiaotian/Develop/personal/stock_analysis/.worktrees/sa-ops
claude
```

首条输入：
```
/loop 你是 Ops agent。读 CLAUDE.local.md 和 ../../docs/agents/common-rules.md 和 ../../docs/agents/transition-checklist.md。执行首轮 bootstrap:(1) ssh jd 探测环境,产出 docs/ops/agent-self-probe.md;(2) 验证 Playwright MCP 可用(browser_navigate http://117.72.184.8:8000/);(3) 创 issue 让 Dev 代为提交。完成后进入稳态循环。优先级:Ready to Verify 队列(调 sub-agent stock-ops-ui-verifier 做 Playwright MCP 验证,T3.6)→ Ready to Deploy 队列(调 sub-agent stock-ops-deployer 部署,T4)→ type:data-bug 队列(数据路径白名单内修复)→ 定时数据巡检(09:35/15:05/18:00/21:30 ScheduleWakeup)。
```

### ④ Auditor 第四（等前三个有运行数据再启）

```bash
cd /Users/xiaotian/Develop/personal/stock_analysis/.worktrees/sa-audit
claude
```

首条输入：
```
/loop 你是 Auditor agent。读 CLAUDE.local.md 和 ../../docs/agents/common-rules.md 和 ../../docs/agents/transition-checklist.md。主 session 每轮做: M1-M10 指标采集(跨 tick 去重,新增 M10:Ready to Verify 队列 > 2h 未处理 → warn) + 产出 docs/ops/audit-log/YYYY-MM-DD.md + 触发告警时建 [AUDIT] issue + Step 4.5 存疑主动告警(打 needs:human-decision,必带 3 选 1 处理建议)。遇用户在任意 issue 下 @Auditor 验 Done,或每月 1 号月度 review,通过 Agent 工具调 sub-agent stock-audit-verifier 做单次动作,拿返回结果后自己 save_comment。进入稳态循环。
```

## 推荐终端布局

**iTerm 或 tmux 4 分屏**：
```
┌─────────────┬─────────────┐
│   PM        │   Dev+Test  │
├─────────────┼─────────────┤
│   Ops       │   Auditor   │
└─────────────┴─────────────┘
```

## 关停与重启

- Ctrl-C 该会话 = 停该 agent
- Linear state 是 append-only，不会因关停污染
- 重启后 agent 从 Linear 当前 state 继续，**无需重新 bootstrap**（除非 state-machine.md 不存在）

## 人工健康检查（5-15 分钟一次）

```bash
# 1. 4 个 worktree 还在
git worktree list | grep '.worktrees'

# 2. Dev / Ops 在推 commit
git fetch origin && git log origin/new-branch --oneline -10

# 3. Linear 状态分布（打开 Linear 看 project 首页）
#    重点看：Ready to Verify 是否有积压 > 1h

# 4. 服务活着
ssh jd "systemctl status $(cat docs/ops/agent-self-probe.md | grep service_name | awk '{print $2}')"

# 5. 今日 audit-log
ls docs/ops/audit-log/ | tail -3
cat docs/ops/audit-log/$(date +%Y-%m-%d).md | tail -30

# 6. 一键扫 4 agent 最近 tick
./scripts/agent_status.sh
```

## 常见问题

**Q: Ready to Verify 积压了，Ops 没有处理？**
A: 检查 sa-ops 终端是否还活着（看 tick 滚动）。若 Ops 打了 `blocked:need-human` 看原因：
- Playwright MCP 不可用 → 检查 Claude Code MCP 设置里 Playwright 是否启用
- 服务不可达 → `curl http://117.72.184.8:8000/` 检查 jd 状态
- FAIL 连续 2 次 → 移 Ready to Verify 回 In Progress，手动 @Dev

**Q: Dev 的验证: 段写了 localhost，Ops 验证失败了？**
A: Auditor M9/C5 会告警。Dev 需修改 comment 把 URL 改为 `117.72.184.8:8000`，Ops 下轮重试。

**Q: UI 验证和人工验证 (verified:human) 有什么区别？**
A: Ops Playwright MCP 验证是**功能正确性**（表格有数据、图表存在、API 响应字段）；`verified:human` 是**你人工点开看过觉得 OK**。两者不互斥，Playwright 通过后进 Ready to Deploy，部署完成进 Done；你可以随时打 `verified:human` 表示人工已确认。

**Q: type:data-bug issue 的流转和普通 issue 一样吗？**
A: 不一样。PM 打 `type:data-bug` 后不走 Dev，直接 sa-ops 认领（state 移 In Progress），在数据路径白名单内修复后直接 Done，不经 T3.6 UI 验证（纯数据 issue 无 UI 改动，SKIP）。

**Q: Dev push 被拒（non-ff）？**
A: Dev 会自动 rebase + 重推；连续 3 次失败 → `blocked:need-human`。

**Q: 为什么必须人工起 /loop 不能自启？**
A: `/loop` 是 Claude Code CLI 交互指令，没有守护模式。想开机自起需要 launchd/tmux resurrect，属下一阶段规划。

**Q: 4 个 agent 并行会不会资源冲突？**
A: 靠**路径白名单 + Linear state 分工**隔离：
- `new-branch` 写权：Dev（业务路径）+ Ops（数据白名单路径）
- PM / Auditor：只读代码，只写 Linear + docs/
- 文件级冲突时后 push 的一方自动 rebase，连续 3 次失败 → `blocked:need-human`

**Q: Playwright MCP 要求 headless 还是有 UI？**
A: headless。Playwright MCP 在 Claude Code 里以 headless 模式运行，不需要显示器。在 Linux jd 服务器上同样适用。
