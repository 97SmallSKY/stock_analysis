# 无人值守 Agent · 启动手册

> 本文件说明如何启动 **4 个** agent。每个在独立的 worktree + 独立的 Claude Code 会话中运行 `/loop` 自驱。
> 你（人）是唯一的进程管理器：开终端、跑 `claude`、发首条 /loop 指令、关终端 = 停 agent。

## 架构速览

```
stock_analysis/                       ← 主仓库（人工介入专用）
├── .worktrees/
│   ├── sa-pm/       CLAUDE.local.md  ← PM agent（Linear 管理）
│   ├── sa-dev/      CLAUDE.local.md  ← Dev+Test agent（实现+合并，唯一写 new-branch）
│   ├── sa-ops/      CLAUDE.local.md  ← Ops agent（部署+判定）
│   └── sa-audit/    CLAUDE.local.md  ← Auditor agent（监察+报警，只读）
└── docs/agents/
    ├── KICKOFF.md                    ← 本文件（你看的）
    ├── transition-checklist.md       ← 状态转换权威门槛（agent 看的）
    └── state-machine.md              ← PM 首轮产出，Linear states 映射
```

所有 agent 只处理：
- Linear team `Personal Dev` (STO)，id `a6a9d55b-9937-4e65-83bd-ac09d20a18e4`
- Linear project `龙虎榜量化交易分析系统`，id `5c09b8da-a3fc-45be-b866-5dc94cf01523`
- **带 label `auto:stock-analysis`** 的 issue（其他全忽略 = 你的人工保留区）

## 5 个标准 Label（PM 首轮自动创建）

| Label | 颜色 | 作用 | 谁打 |
|---|---|---|---|
| `auto:stock-analysis` | 绿 | 项目开关，agent 只处理带此 label 的 | **人工**（你建 issue 时） |
| `type:feature` | 蓝 | 新特性 → Dev 写 CHANGELOG | PM |
| `type:bug` | 红 | bug 修复 → Dev 写 CHANGELOG | PM |
| `type:chore` | 灰 | 重构/文档/基建 → Dev 写 DEVLOG | PM |
| `blocked:need-human` | 黄 | agent 卡死升级人工 | agent 自动 |

## 启动顺序（**严格按顺序，一个一个起**）

### ① PM 先启（bootstrap labels + state-machine 映射）

```bash
cd /Users/xiaotian/Develop/personal/stock_analysis/.worktrees/sa-pm
claude
```

首条输入：
```
/loop 你是 PM agent。读 CLAUDE.local.md 和 ../../docs/agents/transition-checklist.md。执行首轮 bootstrap:(1) 创建 5 个标准 label 若缺失;(2) 写 docs/agents/state-machine.md 映射 Linear states;(3) 创 sentinel issue 贴 state-machine 内容让 Dev 代为提交。完成后进入稳态循环。
```

等你在 Linear 看到 `[PM] bootstrap done` sentinel issue 出现 = PM 就绪。

### ② Dev 次之（接手提交 state-machine.md）

```bash
cd /Users/xiaotian/Develop/personal/stock_analysis/.worktrees/sa-dev
claude
```

首条输入：
```
/loop 你是 Dev+Test agent。读 CLAUDE.local.md 和 ../../docs/agents/transition-checklist.md。首轮先处理遗留:若 sentinel issue 里有 state-machine.md / self-probe.md 内容未 commit,先写文件 + commit + push(type:chore 语义)。然后进入稳态循环。
```

### ③ Ops 再启（bootstrap self-probe）

```bash
cd /Users/xiaotian/Develop/personal/stock_analysis/.worktrees/sa-ops
claude
```

首条输入：
```
/loop 你是 Ops agent。读 CLAUDE.local.md 和 ../../docs/agents/transition-checklist.md。执行首轮 bootstrap:ssh jd 探测环境,产出 docs/ops/agent-self-probe.md,创 issue 让 Dev 代为提交。完成后进入稳态循环。
```

### ④ Auditor 最后（等前三个有运行数据再启也不迟）

```bash
cd /Users/xiaotian/Develop/personal/stock_analysis/.worktrees/sa-audit
claude
```

首条输入：
```
/loop 你是 Auditor agent。读 CLAUDE.local.md 和 ../../docs/agents/transition-checklist.md。按 M1-M8 指标扫描,产出 docs/ops/audit-log/YYYY-MM-DD.md,触发告警时创 [AUDIT] issue 走正常流程。进入稳态循环。
```

## 推荐终端布局

**iTerm 或 tmux 4 分屏**，每屏一个 agent：
```
┌─────────────┬─────────────┐
│   PM        │   Dev+Test  │
├─────────────┼─────────────┤
│   Ops       │   Auditor   │
└─────────────┴─────────────┘
```

这样可以平扫 4 个 tick 的滚动输出，出异常立刻 Ctrl-C 单个。

## 关停

Ctrl-C 该会话 = 停该 agent。Linear state 是 append-only 日志，不会因关停污染。重启后 agent 会从 Linear 当前 state 继续。

## 人工健康检查（5–15 分钟一次）

```bash
# 1. 4 个 worktree 还在
git worktree list | grep '.worktrees'
# 2. Dev 在推 commit
git fetch origin && git log origin/new-branch --oneline -10
# 3. Linear 状态分布（打开 Linear 看 project 首页）
# 4. 服务活着
ssh jd "systemctl status $(cat docs/ops/agent-self-probe.md | grep service_name | awk '{print $2}')"
# 5. 今日 audit-log
ls docs/ops/audit-log/ | tail -3
```

## 常见问题

**Q: 无 `auto:stock-analysis` label 的 issue 怎么办？**
A: 你自己人工处理。agent 完全不碰。PM 发现堆积 > 10 会提醒你，但不会自动加 label。

**Q: Dev push 被拒（non-ff）？**
A: Dev 会自动 rebase + 重推；连续 3 次失败 → `blocked:need-human`。

**Q: PM / Ops / Auditor worktree 能 commit 吗？**
A: 不能。唯一写 new-branch 的是 sa-dev。其他角色的产出物（state-machine / self-probe / audit-log）通过 Linear issue 让 Dev 代为提交（audit-log 也会批量由 Auditor 每日末尾攒一次给 Dev）。

**Q: 三个业务 agent（PM/Dev/Ops）同时跑会打架吗？**
A: 靠 Linear state 互斥 + 代码层只 Dev 写入，不会。Auditor 只读，更不会。

**Q: 暂停某个 agent？**
A: Ctrl-C 该终端。Ready to Deploy 可能堆积，PM/Auditor 会发 comment 告警，但不影响其他 agent。

**Q: 怎么知道 agent 还活着？**
A: 看终端（有 tick 滚动输出）或 Linear（对应 agent 每轮会 comment `@Role tick <ISO8601>`）。Auditor 的 M5 指标会在 > 2h 无 tick 时自动建 `[AUDIT]` severe issue。

**Q: 为什么必须人工起 `/loop` 不能自启？**
A: `/loop` 是 Claude Code CLI 的交互指令，没有守护模式。想开机自起需要 launchd/tmux resurrect，属下一阶段。
