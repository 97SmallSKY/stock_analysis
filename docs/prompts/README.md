# docs/prompts — Agent Sprint Prompt 索引

本目录是 **lhb-quant-v2** 项目喂给 Claude Code / Cursor 等 AI 编码助手的阶段 Prompt。每个 `SXX_*.md` 是一个可独立交付的 Sprint，组合起来构成从数据层到部署的完整开发路径。

---

## 0. 目录结构

| 文件 | 类型 | 作用 |
|------|------|------|
| **[_COMMON.md](_COMMON.md)** | 共用规范 | **所有 SXX 必读前置**：P0 部署闭环、完成信号格式、阶段契约、通用禁止事项、执行流程 |
| [S01_DATA_LAYER.md](S01_DATA_LAYER.md) | Sprint | 数据层：PostgreSQL Schema、Akshare 采集、调度、质量监控 |
| [S02_STRATEGY_TRADING.md](S02_STRATEGY_TRADING.md) | Sprint | 策略 & 交易：信号引擎、状态机、风控、回测 |
| [S03_API_LAYER.md](S03_API_LAYER.md) | Sprint | API 层：FastAPI 路由、认证、WebSocket 实时推送 |
| [S04_FRONTEND.md](S04_FRONTEND.md) | Sprint | 前端：Vue3 Dashboard、ECharts、K 线 |
| [S05_INFRASTRUCTURE.md](S05_INFRASTRUCTURE.md) | Sprint | 基础设施：Docker Compose、Nginx、Redis、部署脚本 |
| [S06_TESTING.md](S06_TESTING.md) | Sprint | 测试：单测、集成、E2E、回测验证 |
| PROGRESS.md | 运行时产物 | 各阶段完成情况（agent 自动追加，人工审阅） |

---

## 1. Agent 使用方式

### 1.1 单阶段执行（推荐）

对 Claude Code 下达指令：

```
请读 docs/prompts/_COMMON.md 和 docs/prompts/S01_DATA_LAYER.md，
严格按 _COMMON.md 的「执行流程」章节完成 S01。
完成后停下等我 review，不要进入 S02。
```

这是**默认推荐方式**——金融项目错误会复利，跨阶段必须人工放行。

### 1.2 Prompt 结构约定

每个 `SXX_*.md` 都遵循以下结构（优化版）：

```
0. 前置阅读          ← 必须先读的架构 / 设计文档
1. 任务目标          ← 一句话说清本阶段要解决什么
2. 输入依赖          ← 需要的上游产物（对应 _COMMON.md §3 契约表）
3. 具体任务          ← 实现细节、接口签名、约束
4. 验收标准（完成信号）  ← 可机器检测的断言，不是 "pytest 不报错"
5. 禁止事项          ← 本阶段特有的红线（通用红线在 _COMMON.md §4）
6. 输出产物          ← 供下一阶段消费的文件/表/接口
```

### 1.3 阶段间契约

S01 → S02 → ... → S06 的接口契约由 [_COMMON.md §3](_COMMON.md) **集中定义**，不在各 SXX 内重复。下一阶段启动前 agent 必须跑「契约自检」命令，失败则直接回到上一阶段修复。

### 1.4 P0 部署闭环

**每轮代码修改完成**后 agent 必须执行 [_COMMON.md §1](_COMMON.md) 的 rsync + 重启 + curl 验证三步。跳过此步 = 本轮工作**无效**，因为线上服务器看不到变更。

---

## 2. 完成信号（核心改进）

历史上这批 prompt 最大的问题是 `验收标准` 写成了 `pytest -v` / `make test` 这种**弱信号**——测试 0 个用例也能 pass，agent 会自欺通过。新规范要求每条验收都是：

```bash
# 具体 + 可重复 + 失败即停
psql $DATABASE_URL -tAc "SELECT COUNT(*) FROM lhb_records" | awk '$1>=100{exit 0}{exit 1}'
curl -sf http://localhost:8000/health | jq -e '.code==200'
grep -rn "FLOAT" backend/db/migrations/ && exit 1 || echo "✅"
```

详见 [_COMMON.md §2](_COMMON.md)。

---

## 3. 进度跟踪（PROGRESS.md）

每完成一个阶段，agent 在 `PROGRESS.md` 追加：

```markdown
## S01 — 数据层
- 完成时间：2026-04-17T18:32:00+08:00
- 验收输出：[完成信号 stdout]
- 部署验证：curl 117.72.184.8:8051 → 200 @ 18:35
- 遗留问题：无
```

新开对话时，agent 先读 PROGRESS.md 确定从哪里继续，避免重复劳动。

---

## 4. 参考文档（业务设计源头）

- 架构总览：`docs/lhb-quant-v2/ARCHITECTURE.md`
- 数据库设计：`docs/lhb-quant-v2/DATABASE.md`
- 策略逻辑：`docs/lhb-quant-v2/STRATEGY.md`
- 交易流程：`docs/lhb-quant-v2/TRADING_FLOW.md`
- 风险控制：`docs/lhb-quant-v2/RISK_CONTROL.md`
- API 规范：`docs/lhb-quant-v2/API.md`

Prompt 与上述文档冲突时，**以文档为权威**——prompt 只是执行指南，业务定义来自 `docs/lhb-quant-v2/`。

---

## 5. 给维护者：如何改这批 Prompt

1. **共用规则改 `_COMMON.md`**，不要拷贝到各 SXX——重复 = 漂移风险
2. **业务参数改 `docs/lhb-quant-v2/`**，不要写进 prompt——prompt 改动难追溯
3. **完成信号只加不减**——每条都是曾经踩过的坑，删一条 = 放一个洞
4. **SXX 顺序不得调换**——S02 依赖 S01 产出，跳序执行契约自检会直接失败
