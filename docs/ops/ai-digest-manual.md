# AI Digest · 使用手册

> **脚本**:[`scripts/ai-digest.py`](../../scripts/ai-digest.py)
> **用途**:4 角色挂机系统的**每日健康巡检 + 规则违反告警**,取代 Supervisor 角色。
> **哲学**:**观察而不干预**。发现异常只写日报 + 建 🔴 issue 升级给用户,不直接改业务 issue。

---

## 📐 为什么要这个脚本?

4 角色挂机(PM / Dev / Test / Ops)每个都只看自己的队列,**没人抬头看全局**,会有:

1. **死锁**:PM+Dev 互相等对方回,24h 没人动
2. **规则违反**:有人跳过 Test 签字直接转 Done / Watch issue 被误关
3. **标签清洁度**:Done 还挂 @ops / 孤儿 issue 超 24h 没被 PM 捞
4. **质量失控**:Bug 激增 / 同一模块反复修
5. **系统健康**:Watch 减少 / 项目长时间无活动

**Supervisor 角色 = 加一份进程 + token + 规约**,不划算;**日报脚本 = 零成本,每天 cron 跑一次即可**。

---

## ⚙️ 前置:Linear Personal API Key

### Step 1:获取

1. 登录 https://linear.app/xiaotian-dev
2. 左下头像 → **Settings** → **Security & access** → **Personal API keys**
3. 点 **New API key**,输入名字(如 `ai-digest`),**Scope 选择最小权限**:
 - Read(必选)
 - Write - Issues(建 🔴 issue 需要)
4. 复制 `lin_api_xxxxx...` 开头的字符串,**只显示一次**

### Step 2:放 .env

```bash
# 编辑 .env(不要 commit,`.gitignore` 已排除)
LINEAR_API_KEY=lin_api_xxxxx
LINEAR_TEAM_ID=a6a9d55b-9937-4e65-83bd-ac09d20a18e4
LINEAR_PROJECT_ID=5c09b8da-a3fc-45be-b866-5dc94cf01523
AI_DIGEST_AUTO_ESCALATE=true
AI_DIGEST_OUTPUT_DIR=docs/ops/digest
```

### Step 3:依赖

```bash
pip install requests python-dotenv
```

---

## 🚀 使用

### 最常用:跑今天的日报

```bash
./scripts/ai-digest.py
```

输出:
- 终端打印 Markdown 日报
- 文件写到 `docs/ops/digest/YYYY-MM-DD.md`
- 如有 `critical` 异常,自动建 `🔴 [AI-Digest] ...` issue,挂 Bug + Urgent + @pm

### dry-run(试一下不写文件 / 不建 issue)

```bash
./scripts/ai-digest.py --dry-run
```

### 只扫最近 N 小时

```bash
./scripts/ai-digest.py --hours 48
```

### 临时不建 🔴 issue(避免骚扰)

```bash
./scripts/ai-digest.py --no-escalate
```

---

## ⏰ Cron 配置(每天早上 8:00)

### macOS

```bash
# 编辑当前用户 crontab
crontab -e

# 加入一行(路径根据你实际的替换):
0 8 * * * cd /Users/xiaotian/Develop/personal/stock_analysis && ./scripts/ai-digest.py >> logs/ai-digest.log 2>&1
```

### Linux(若以后迁 jd)

```bash
# /etc/cron.d/ai-digest
0 8 * * * xiaotian cd /home/xiaotian/stock_analysis && ./scripts/ai-digest.py >> logs/ai-digest.log 2>&1
```

### 验证 cron 生效

```bash
# 看 cron 下一次执行时间
crontab -l | grep ai-digest

# 看最近一次执行日志
tail -50 logs/ai-digest.log
```

---

## 📊 5 类异常详解

### 1. 死锁(deadlock)

**检测**:issue 挂 ≥ 2 个角色标签(如 `@pm` + `@dev`)且 `updatedAt` 距今 ≥ 24h

**严重度**:
- ≥ 24h 且 < 48h → `warn`
- ≥ 48h → `critical`(自动建 🔴 issue)

**误报可能**:PM 扔球给 Dev 后 Dev 真在慢慢做,没评论。若频繁误报,调大 `DEADLOCK_HOURS_THRESHOLD`。

---

### 2. 规则违反(rule-violation)

**检测**:
- Done 但无 `test-passed` 标签(越权转 Done)
- Watch issue 被错误转 Done(Watch 应常驻 In Progress)

**严重度**:全部 `critical`(自动升级)

**例外白名单**(不报警):
- `Docs` 类 issue 可以跳过 test-passed(文档改动)
- `[Watch]`, `📢`, `🔴 [AI-Digest]` 前缀的 issue(规约通告、巡检、告警本身)
- `Blocked` 标签的 issue(被阻塞,可能特殊流程)

---

### 3. 标签清洁度(cleanliness)

**检测**:
- Done 还挂 `@role` 标签(未清理)
- Backlog 孤儿 issue(无任何角色标签)超 24h 没被 PM 捞

**严重度**:`warn`(只日报,不升级)

---

### 4. 质量失控(quality)

**检测**:
- 打了 `test-passed` 但评论里没有 pytest/覆盖率数字(低质量签字)
- 最近 7 天新增 Bug ≥ 8 条(增速异常)
- 同一模块(按标题 cluster)最近 7 天出 ≥ 3 个 bug

**严重度**:`warn`

**「模块 cluster」怎么算**:提取标题前 2 个中/英文词做 key,相同 key 的归为一个模块。
粗糙但够用。若误报多,换成按 issue label 做 cluster。

---

### 5. 系统健康(health)

**检测**:
- Watch issue 数量 < 2(预期至少 STO-38 daily + STO-44 schema drift)
- 整个项目 ≥ 72h 无任何 issue 更新(4 角色可能全停)

**严重度**:`warn`

---

## 🔴 自动升级建 issue 的机制

当 `AI_DIGEST_AUTO_ESCALATE=true` 且发现 `critical` 异常时,脚本会:

1. 建一个 `🔴 [AI-Digest] <title>` issue
2. 标签:`Bug` + `Urgent` + `@pm`
3. 描述包含:类别 / 详情 / 证据 JSON / 相关 issue ID
4. 优先级:Urgent(1)

PM 看到 🔴 issue 后,按 pm.md §5 L4 流程处理:
- **真异常**:按情况改规约 / 撤销操作 / 补流程
- **误报**:移除 🔴 + Urgent,评论说明用于改进 digest 脚本

---

## 🧯 常见问题

### Q1:跑出来一堆误报怎么办?

- 先用 `--dry-run` 看看
- 如果某类异常误报多,编辑 `scripts/ai-digest.py` 里对应的 `detect_xxx()` 函数,调整阈值或白名单
- 脚本和业务解耦,可以随便改,不会影响挂机 AI

### Q2:能否只发日报不建 issue?

```bash
./scripts/ai-digest.py --no-escalate
```

或在 `.env` 里设 `AI_DIGEST_AUTO_ESCALATE=false`。

### Q3:脚本依赖 MCP 吗?

**不**。脚本直接调 Linear GraphQL API(`https://api.linear.app/graphql`),不走 MCP。
MCP 只在 Claude 对话里可用,cron 调不了。

### Q4:如何验证某个检测规则没漏?

构造一个违反规则的测试 issue(如故意把 Watch 转 Done),跑 `--dry-run`,看是否报。
测试完把它改回去。

### Q5:为什么不用 Linear 官方 Python SDK?

Linear 官方没有维护的 Python SDK,第三方包(如 `linear-api`)滞后严重。
用 `requests` + GraphQL query 更稳定 + 零黑箱。

---

## 📅 典型日报示例

见 `docs/ops/digest/` 目录下最新的 `.md` 文件。

---

## 🔄 脚本维护

### 新增一类检测

1. 在 `scripts/ai-digest.py` 中加 `detect_xxx(issues) -> list[Anomaly]`
2. 在 `build_report()` 里调用
3. 决定严重度(critical → 升级,warn → 只日报)
4. 更新本手册 §5 类异常详解

### 修改白名单 / 阈值

搜 `DEADLOCK_HOURS_THRESHOLD` / `ORPHAN_HOURS_THRESHOLD` / `is_exception` 等变量。

### Linear API 改了?

脚本里的 GraphQL query 集中在:
- `FETCH_ISSUES_QUERY`
- `CREATE_ISSUE_MUTATION`
- `fetch_label_map()` 里的内联 query

如 Linear 改 schema,改这几处即可。

---

## 📝 变更日志

- **2026-04-20** 初版:5 类异常检测 + 自动升级 + cron 配置
