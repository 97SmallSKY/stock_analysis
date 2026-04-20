# 本地 4 角色 AI 挂机使用手册

> **场景**:macOS 本地同时跑 4 个 Claude Code 会话(PM / Dev / Test / Ops),各自扮演一个角色,每 10 分钟自动轮询 Linear 处理自己队列。
>
> **先决条件**:
> - 已安装 Claude Code CLI(`claude` 命令可用)
> - 已配置 Linear MCP(当前已配)
> - 本仓库已有 `docs/prompts/linear/*.md` 规约

---

## 🚨 重要限制(先读)

1. **macOS 系统睡眠会暂停 AI** —— 启动脚本已自动运行 `caffeinate -i` 防睡眠,但**关盖就停**
2. **4 个 Terminal 窗口必须保持开启** —— 关窗等于杀进程,AI 立刻停止
3. **API 费用按调用计算** —— 4 角色 × 每 10 分钟 × 每天 144 次 = **576 次/天**,根据 prompt 长度每次约 $0.05-0.20,预算**每天 $30-60** 上下
4. **不要在同一 Claude Code 里跑多个角色** —— 会污染上下文 + 违反「三方共识」设计
5. **/loop 模式里的 AI 仍受 token 上下文窗口限制** —— 长时间跑会膨胀,建议**每 12h 手动重启一次会话**(stop + start)

---

## 🚀 启动

### 一键启动 4 个

```bash
cd ~/Develop/personal/stock_analysis
./scripts/start-ai-agents.sh
```

执行后会:
1. 启动 `caffeinate -i` 防 macOS 睡眠(PID 写 `/tmp/ai-agents-caffeinate.pid`)
2. 依次打开 4 个 Terminal.app 窗口(标题:AI-PM / AI-DEV / AI-TEST / AI-OPS)
3. 每个窗口 `cd` 到 repo 后启动 `claude`
4. 生成 `/tmp/ai-agent-<role>-prompt.txt` 开场白文件

### 在每个新窗口手动完成 3 步

脚本不能自动把 prompt 粘进 claude(Terminal 输入到交互式程序很脆弱),所以每个窗口需要手动:

```bash
# Step 1:等 claude 提示符出来

# Step 2:复制开场白到剪贴板(在 claude 的 shell mode 下跑)
cat /tmp/ai-agent-pm-prompt.txt | pbcopy
# 或直接 cat 看一眼,手动选择复制

# Step 3:在 claude 输入框粘贴(Cmd+V),回车
```

AI 收到开场白后会:
1. 读 `linear/README.md` + `linear/<role>.md` + `CLAUDE.md`
2. 执行第一轮轮询
3. 自动进入 `/loop` 模式等下一轮

### 只启动某几个角色

```bash
./scripts/start-ai-agents.sh dev test       # 只启动 Dev 和 Test
./scripts/start-ai-agents.sh ops             # 只启动 Ops
```

---

## 📊 监控

### 查看每个角色的活跃状态

在 Claude Code 窗口里看日志(AI 按规约每轮会打印 `[HH:MM] ROLE cycle: ...`)。

### 查看 Linear 中各队列

在 Linear 网页端建 5 个 filter view(见 `linear/README.md §4.8`):

- 🔴 等我决策:`label = @pm AND label = Urgent`
- 🟠 Dev 队列:`label = @dev AND label != test-passed`
- 🟣 Test 队列:`label = @test OR (state = In Review AND label != test-passed AND label != @test)`
- 🔴 Ops 队列:`label = @ops AND (label != Blocked OR state != In Progress)`
- ✅ 已签字:`label = test-passed`

### 验证 AI 真的在跑

```bash
ps -ef | grep -i claude | grep -v grep
# 应看到 4 个 claude 进程

ps -ef | grep caffeinate | grep -v grep
# 应看到 1 个 caffeinate -i 进程
```

---

## 🛑 停止

### 全部停止

```bash
./scripts/stop-ai-agents.sh
```

执行会:
1. 杀 `caffeinate` 进程(恢复系统睡眠)
2. 关闭标题以 `AI-` 开头的 Terminal 窗口
3. 清理 `/tmp/ai-agent-*-prompt.txt`

### 只停某一个角色

直接关掉对应 Terminal 窗口(`Cmd+W`)即可。

---

## 🔄 每 12h 定期重启(推荐)

每个 Claude Code session 会累积上下文,跑太久会 token 爆 / 回答质量下降。建议每 12h 重启一次:

```bash
./scripts/stop-ai-agents.sh && sleep 5 && ./scripts/start-ai-agents.sh
```

也可以加到 `crontab`(如果你确定电脑不会关机):

```
# 每天 06:00 和 18:00 重启
0 6,18 * * * cd ~/Develop/personal/stock_analysis && ./scripts/stop-ai-agents.sh && sleep 10 && ./scripts/start-ai-agents.sh
```

---

## 🧯 常见问题

### Q1:电脑重启后怎么办?

开机后重新跑 `./scripts/start-ai-agents.sh` 即可。所有状态都保留在 Linear,AI 重启后会读标签接着干。

### Q2:某个角色卡死了?

找到对应 Terminal 窗口,`Ctrl+C` 中断,然后重新粘贴开场白。

### Q3:4 个 AI 会同时改同一个 issue 吗?

**可能性低但存在**。Linear API 的 save_issue 是最后写入胜出,会产生标签覆盖。规约里已规定:
- PM 不碰 @dev / @test / @ops 的队列
- Dev 不碰 @test / @ops 的队列
- 等等

每个角色只操作**自己标签的 issue**,冲突概率极低。若出现,以 Linear UI 最新状态为准。

### Q4:怎么知道 AI 在偷懒 / 跑飞了?

- Linear 看板出现大量 `@pm` 堆积 = PM 在正常分流(OK)
- 出现 🔴 标记 = PM 请你决策(按手册规约动作)
- `@test` 越堆越多 = Test 没跟上,检查 Test 会话是否还在轮询
- `test-passed` 但长时间未 Done = Ops 没部署,检查 Ops 会话

### Q5:费用失控怎么办?

- 立刻 `./scripts/stop-ai-agents.sh`
- 检查 Anthropic console 看本月消耗
- 调大轮询间隔:改 start-ai-agents.sh 里 `delaySeconds=600` 为更大值
- 只跑关键角色:`./scripts/start-ai-agents.sh dev ops`(跳过 PM/Test)

### Q6:为什么不部署到 jd 服务器?

可以,但需要:
- jd 装 claude CLI(需要 API key 环境变量)
- tmux 代替 Terminal.app
- 注意 jd 的 SG 代理配置(Claude API 可能需要走代理)

见 `docs/ops/ai-agents-jd-deploy.md`(未写,TODO)。

---

## 📋 每角色做什么的速查

### PM(每 10 分钟)
- 扫 `@pm` 标签 issue,按 pm.md §3.9 决策边界处理
- L2/L3 自己拍板 + 评论留痕
- L4 升级:加 🔴 + Urgent + AskUserQuestion
- 空闲时扫 PROGRESS.md / commit 识别新技术债

### Dev(每 10 分钟)
- 扫 `@dev` 标签的 Backlog/Todo,按优先级拉最高优
- 一轮最多做完 1 个 issue
- 提 PR 后加 `@test` + `@ops`,评论 @test 请测

### Test(每 10 分钟)
- 扫 `@test` 标签(P0)
- 扫 `In Review AND NOT test-passed`(P1,主动巡检)
- 按 test.md §3.10 写 5 项测试用例 → 执行 → 打 `test-passed`
- 用户撤回 test-passed 时按 §3.11 分两路处理

### Ops(每 10 分钟)
- 按 7x24_agent.md 跑健康巡检(最优先)
- 扫 `@ops AND test-passed` 队列 → 部署
- 扫 Watch issue(`label=@ops AND Blocked AND In Progress`)→ 追加巡检记录
- 转 Done 前必看 test-passed 标签

---

## ✅ 首次启动自检 checklist

- [ ] `claude --version` 能跑
- [ ] Linear MCP 已配(在任意 claude 会话里试 `mcp__linear__list_issues`)
- [ ] `docs/prompts/linear/README.md` 存在
- [ ] Anthropic API key 已配 + 有余额
- [ ] 电源设置:连接电源时不自动睡眠(系统偏好 → 电池 → 电源适配器)
- [ ] 已跑 `./scripts/start-ai-agents.sh` 且 4 个窗口都开了
- [ ] 每个窗口都粘贴了 prompt 并看到 AI 响应
- [ ] Linear 看板确认第一轮有活动(新评论 / 标签变更)

---

## 📝 变更日志

- **2026-04-20** 初版:覆盖 4 角色本地挂机完整流程
