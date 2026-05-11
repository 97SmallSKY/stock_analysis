# 2026-05-01 无人值守 Agent 方案明显问题审查

> 审查对象: `docs/agents/KICKOFF.md` 及相关 `common-rules.md` / `transition-checklist.md` / `state-machine.md` / `edge-cases.md`。
> 目标读者: PM / Dev / Ops / Auditor。
> 结论: 当前方案已经覆盖了角色分工和主要状态门槛, 但仍有若干结构性问题。如果不先修订, 4-agent 运行会在状态流转、验证有效性、部署安全和长期无人值守上反复出错。

## 总体判断

该方案的问题不在"是否有流程", 而在流程之间存在明显不一致, 且把关键保障放在人工记忆、Linear comment、单点终端和弱 smoke 上。它适合短期人工盯守试运行, 但还不能称为稳定的 7x24 无人值守系统。

优先级最高的修订点(按落地顺序):

1. **P0** 前置依赖脚本: 修 `deploy.sh`、`smoke_http.sh` 分层、`smoke_chunks.py`、M14 检测、heartbeat、artifact 清理。脚本不存在则 agent 第一个 tick 就 bail out。
2. **P0** Label 改 name 匹配 + bootstrap 实时读取, 消除静态快照漂移。
3. **P0** 修正 state flow 顺序 + T3.6 改为 post-deploy 验证 + `deploy.sh --sha` 精确部署。
4. **P0** MCP/Tool 统一错误契约写入 `common-rules.md`。
5. **P1** S/M/L/R/H 分级 + Dev 越界检测 + Dev `@Ops ready` 分层验证指令。
6. **P1** Auditor 角色卡修订(只保留 M1-M11+M14)+ 审计日志文件名改为 `{date}-{issue-id}.md`。
7. **P2** 新增 `refactor-lane.md`、`architecture-gates.md`、`bail-out-rules.md`。

---

## P0-1 state-machine 里的 label id 明显冲突

### 现象

`docs/agents/state-machine.md` 中:

- `type:bug` 的 id 是 `a34dd1c2-ce9e-40b2-aac8-e6c63a9ee4bd`
- `verify:pass` 的 id 也是 `a34dd1c2-ce9e-40b2-aac8-e6c63a9ee4bd`

两个不同语义 label 共享同一个 Linear label id。

### 影响

如果 agent 按文档 id 操作 label:

- Ops 想打 `verify:pass`, 实际打成或覆盖 `type:bug`。
- Auditor 对 Done issue 的 `verify:pass` 检查失真。
- PM bootstrap 以为 label 已存在, 实际映射错误。

### 根因分析

`state-machine.md` 里写死 label id 是根本问题。Linear 是真正的事实来源, 文档里的 id 只是某次读取的快照, 随时可能过期。

**错误的修复方向**: 加 `verify_labels.py` 脚本来"同步"文档——这是承认快照会漂移后再加补救层, 不如消除静态快照本身。

**正确的修复方向**: `state-machine.md` 里不写 label id。Agent 每次 bootstrap 时从 Linear MCP `list_issue_labels` 实时读取, 用 **name 匹配**而非 id 匹配。

**防御层**: Linear MCP 存在返回假空的已知 bug。读取结果为 0 个 label 时, agent 必须视为"读取失败"并重试, 不能视为"label 不存在"。读取 0 连续 3 次, 则 `blocked:need-human`, 不进入稳态循环。

### 修订建议

1. 从 `state-machine.md` 的 label 表中删除 id 列, 只保留 name 和语义。
2. PM bootstrap 改为: 调用 `list_issue_labels` 实时获取 → name 匹配 → 缺失则创建。
3. bootstrap 完成的判断条件: 11 个 label 全部能用 name 查到(而非"文件存在")。

---

## P0-2 状态流转图前后矛盾

### 现象

`KICKOFF.md` 和 `transition-checklist.md` 的主流程是:

```text
In Progress -> In Verification -> In Review -> Done
```

但 `state-machine.md` 的"流转路径"写成:

```text
Backlog -> Todo -> In Progress -> In Review -> In Verification -> Done
```

顺序相反。

### 修订建议

`state-machine.md` 流转路径统一修正为:

```text
Backlog -> Todo -> In Progress -> In Verification -> In Review -> Done
```

每个 state 的语义:
- `In Verification`: Dev push 完成, 等待 Ops 验证(含部署 + post-deploy Playwright)
- `In Review`: Ops 验证通过, 等待最终 Done 确认(≤ 5 分钟自动推进)
- `In Review` 不是代码 review, 是 Ready to Deploy 的语义槽

如果要进一步简化可以废弃 `In Review` state, 但需要同步修改 `state-machine.md` 里 6 个 state 的完整语义定义, 不在本文件范围内。

---

## P0-3 T3.6 在部署前验证线上 jd, 容易验证旧版本

### 核心问题: Linear state 与 git 状态异步

Linear state 不反映 git 或部署状态:
- `In Verification` 不意味着 commit 已推到 `origin/new-branch`
- `Done` 不意味着 jd 服务器已运行对应 commit

**唯一可靠的绑定点是 commit SHA**。Dev 的 `@Ops ready` comment 必须包含 commit SHA。Ops 的所有后续操作以这个 SHA 为锚点, 不以 Linear state 为锚点。

真正的风险不是"Dev 移 In Verification 后又 push 了新 commit"(T3 checklist 应显式禁止移 In Verification 后追加 push), 而是"Dev 的 push 本身失败了但自己不知道"。`git merge-base --is-ancestor` 覆盖了这个场景: SHA 不在远端 → 非祖先 → 阻塞。

### 修订建议: 精确部署指定 SHA

**流程改为**:

```text
Dev push (SHA=abc123) -> In Verification
  -> Ops: git merge-base --is-ancestor abc123 origin/new-branch (验证 SHA 真的在远端)
  -> Ops: deploy.sh --sha abc123  (精确部署, 不部署分支最新 HEAD)
  -> Ops: ssh jd "git -C ~/stock_analysis log -1 --format=%H" 确认部署版本 = abc123
  -> Ops: healthcheck 通过后 post-deploy Playwright 验证
  -> Done
```

**`deploy.sh` 修改**: 接受可选参数 `--sha <sha>`。有参数时 `git fetch && git checkout <sha>`, 无参数时维持 `git pull`(兼容现有人工部署流程)。Ops 在自动化路径里总是传 `--sha`。不用 detached HEAD——改为 `git fetch && git -C ~/stock_analysis reset --hard <sha>`(在已 checkout 的 new-branch 上 reset, 避免 detached HEAD 导致下次 pull 失败)。

**部署版本确认**: `ssh jd "git log -1"` 确认代码版本, 但 `deploy.sh` 的 healthcheck 必须等待服务真正接受请求后再返回 0, 不能 deploy.sh 返回 0 就立即做 Playwright 验证。

**T3 checklist 新增约束**: Dev 移 `In Verification` 后, 禁止在同一 issue 上追加 push 新 commit。若需修改, 必须由 Ops 将 issue 移回 `In Progress` 后再操作。

**artifact 路径**: 改用 `{issue_id}/{commit_sha_short}/` 而非 `{issue_id}/{timestamp}/`, 防止并发时路径碰撞, 且与 git 历史可追溯。

---

## P0-4 T4 smoke 覆盖太弱, 已被线上问题证明会漏检

### 现象

T4 只探活 SPA index.html + JS bundle。实际已发现:

```text
/assets/BacktestRun-Q_5U7FMf.js -> 404
/assets/BacktestRun-CaBUNYl3.css -> 404
```

### 根因: SPA fallback 导致路由 smoke 必定误报通过

`backend/core/rate_limit.py` 对非 `/api` 路径做了 SPA fallback——`curl /backtest-run` 返回 `index.html`(200), 永远不是 404。**扩展路由 + 检测 HTTP >= 400 对懒加载 chunk 完全无效**。

真实根因是 `deploy.sh` 没有完整重建 `dist/` 目录——Vite 构建产生新 hash 的 chunk, 但旧 hash 文件未被正确部署。

### 修订建议: smoke 重新定义为两层

**L1(API 存活)**: `curl` 所有 API 端点, 检测 HTTP 200。现有 `smoke_http.sh` 已覆盖, 保持不变。

**L2(前端资源完整性)**: 从 `smoke_http.sh` 分离, 用独立 `scripts/smoke_chunks.py` 实现。

实现方式: Playwright 加载页面, 监听 `page.on('response')` 事件, 检测任一子请求 status >= 400 即失败。不需要改 Vite 构建配置, 直接从用户视角验证。

**运行环境**: `smoke_chunks.py` 在**本机执行**，不在 jd 服务器上跑。本机已有 Playwright MCP，能访问 `117.72.184.8:8000` 即可运行，不需要 jd 安装任何依赖。

**T4 checklist 对应修改**: `smoke_http.sh exit 0` 之后增加 `smoke_chunks.py exit 0`。

两个并行修复:
1. **修 `deploy.sh`**: 每次部署完整执行 `npm run build` 并用新 `dist/` 完全替换旧目录。
2. **新增 `smoke_chunks.py`**: 覆盖所有一线路由懒加载 chunk 的可达性验证。

---

## P0-5 `/tmp/ui-verify` 作为截图存储不可追溯

改为持久 artifact 路径, 以 commit SHA short 作为目录名:

```text
~/stock_analysis_artifacts/ui-verify/{issue_id}/{commit_sha_short}/
```

Linear comment 写入: artifact 路径、commit SHA、route、viewport、截图文件名、network error 摘要。
成功保留 7 天, 失败保留 30 天, 由 Ops 定期清理。

---

## P1-1 7x24 依赖人工终端, 与"无人值守"目标冲突

### 根因分析

`/loop` 是 Claude Code 的交互指令, 依赖 Claude API session, **不是可以被 systemd/launchd 管理的独立进程**。进程守护方案在这里不可行。

### 修订建议

将 KICKOFF.md 的"7x24 无人值守"改为"人工值守长跑试运行":

- 用 tmux 4 分屏, 但不宣称这等于守护进程。
- 每个 agent 每轮 tick 写 heartbeat 文件 (`~/heartbeat-{role}.txt`) 更新时间戳。
- 人工用 `./scripts/agent_status.sh` 检查 heartbeat 是否在最近 N 分钟内更新。
- 重启后恢复策略: 从 Linear 当前 state 继续, 不重复 bootstrap(除非 11 个 label 用 name 查不到)。

---

## P1-2 PM / Auditor 写 docs 与"只读代码"的权限边界不清

### 技术精确化

git worktree 的每个分支检出到独立物理目录, 本地文件不共享。冲突不发生在本地写阶段, 而是发生在**推送阶段**: 多个 worktree 各自 commit 后 push 到同一个 `origin/new-branch`, 第二个 push 会因远程已前进而被拒(non-fast-forward)。

### 修订建议: 缩小写者范围

- `CHANGELOG.md` / `DEVLOG.md` / `PROGRESS.md`: Dev 独占写权, 在 T3 push 时一次性写入。PM/Ops/Auditor 不写, 避免同一时刻多人 push 冲突。
- `docs/ops/audit-log/`: Auditor 写, 文件名改为 `YYYY-MM-DD-{issue-id}.md`(独立文件名天然避免 push 冲突)。
- `docs/ops/agent-self-probe.md`: Ops 写, 独立 commit。
- `state-machine.md`: 只在 bootstrap 阶段由 Dev 一次性 commit, 稳态中不再修改。
- 明确写权: 只有 Dev 和 Ops 有常规写权限, PM 无, Auditor 只写 `docs/ops/`。

若后续 Dev + Ops 的 push 冲突仍频繁, 改为"每完成 3 个 issue 或每 2 小时集中写一次 CHANGELOG/PROGRESS", 不用"每天收工 tick"(agent 没有收工概念)。

---

## P1-3 type:data-bug 允许 Ops 直接 Done, 测试门槛不足

保留 SKIP UI, 但必须增加 API/data smoke:

- `curl /api/dashboard`
- `curl /api/lhb?limit=1`
- `curl /api/reports/weekly`
- 受影响数据表的 row count / max date / null ratio 检查

---

## P1-4 T3 只跑后端 pytest, 前端变更缺强制验证

T3 增加条件:

- 若改动 `frontend/**`, 必跑 `npm --prefix frontend test`。
- 必跑 `npm --prefix frontend run build` 或 `vue-tsc --noEmit`。
- 若只是 docs, 可明确 SKIP 并在 `验证:` 段注明。

---

## P1-5 comment 长度规则与验证报告冲突

新增 E 型"验证报告 comment"模板: 摘要 ≤ 12 行, 详细报告写 artifact 路径。C2 的 ≤ 5 行规则不适用于 E 型 comment。

---

## P2-1 健康检查命令依赖未必存在的 agent-self-probe.md

```bash
SVC=$(grep -s service_name docs/ops/agent-self-probe.md | awk '{print $2}')
ssh jd "systemctl status ${SVC:-stock_analysis_api.service}"
```

---

## P2-2 label 数量与 bootstrap prompt 不一致

PM 首条 prompt 改为"确认 11 个标准 label 存在", 与 KICKOFF.md 标题一致。

---

## Refactor Lane 的"独立回滚"要求不切实际

改为: 尽量分 commit; 无法分开时在交接 comment 里说明原因, Ops 做好整体 revert 准备。

---

## 附: 系统级根本限制(修订无法解决)

以下问题是 LLM agent 能力边界的固有限制, 应显式承认而非假装可以通过流程修订解决。**治理方案不应在承认这些限制的同时, 继续设计依赖跨 agent 上下文传递的复杂流程**。

### 1. 验证闭环的错误相关性问题

当写代码的 AI 和验证代码的 AI 基于同一份有损自然语言(AC)工作时, 错误相关性比人类团队更高(共享同一底层模型的训练偏置, 倾向于犯同一类错误)。

**可行的缓解方向**:
- Ops 验证时不读 AC, 只从用户视角操作页面——让验证者不知道实现者的假设。
- 但"页面能打开"不等于"数据算对了"。对数据口径/计算公式变更, Dev 必须在 `@Ops ready` 里提供可机器判定的断言(见 governance 文档"分层验证指令"章节)。
- Auditor 用脚本(非 LLM)做交叉检查——引入非 LLM 的异构验证。

**无效方向**: 引入"不可变的测试基准"——如果写基准的人同样误解了 AC, 不可变只是把错误锁死。

### 2. 时间模型的不确定性

4 个 agent 混用时间驱动(定时 ScheduleWakeup)和事件驱动(Linear state 变化)。同一个 issue 进入 `In Verification` 后, 被 Ops 处理的延迟在 0 秒到数小时之间不确定, 无法给出 SLA。

### 3. `verify:pass` 不等于功能正确

累积的自然语言翻译误差(PM→AC→Dev→实现→Ops→验证)可能产出一个"全链路测试通过但业务逻辑彻底错误"的 Done issue。唯一的真实检查点是用户打开浏览器看到结果。

### 4. LLM 长周期状态保持能力不足

这是当前治理方案最根本的矛盾: 承认了"记不住", 然后设计了更复杂的跨 agent 上下文传递。解决方向不是加更多流程, 而是让每一步的产出物足够简单、机器可读(脚本输出 → agent 读 stdout, 而不是 agent 读 agent 的自然语言 comment)。
