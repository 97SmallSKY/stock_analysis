# _COMMON.md — Sprint Prompt 共用规范

> 所有 S01–S06 Prompt **默认继承本文件**。Agent 在执行任何 `SXX_*.md` 前必须先读完本文。

---

## 1. ⚠️ P0 部署闭环（每轮修改必须执行，不得跳过）

项目运行在远程服务器（`ssh jd`，`~/stock_analysis`），本地改动**不会**自动同步。任何一轮代码修改完成后，agent 必须执行：

```bash
# Step 1：rsync 同步
rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.git' --exclude='.claude/' --exclude='data/' --exclude='logs/' \
  ./ jd:~/stock_analysis/

# Step 2：重启服务
ssh jd "cd ~/stock_analysis && ./stop.sh && sleep 1 && STREAMLIT_PORT=8051 ./start.sh"

# Step 3：线上验证（必须看到 200 响应）
curl -sS -o /dev/null -w "%{http_code}\n" http://117.72.184.8:8051/ | grep -q 200 \
  && echo "✅ 线上服务存活" || (echo "❌ 线上服务未响应" && exit 1)
```

**判断"一轮修改完成"的标准：** agent 认为本次对话的代码变更已达到可验收状态，不论涉及文件多少。

---

## 2. 完成信号（Completion Signals）

每个阶段的 `验收标准` 章节必须是**可机器检测**的断言，不能是 `pytest 不报错` 这种弱信号。每条完成信号必须满足：

- **具体化**：有明确的 grep pattern / SQL 查询 / HTTP 状态码 / 文件存在检查
- **可重复**：同样输入必然产生同样输出
- **失败即停**：任一条失败则本阶段**未完成**，不得进入下一阶段

**完成信号格式约定：**

```bash
# ✅ 好的信号：
psql $DATABASE_URL -tAc "SELECT COUNT(*) FROM lhb_records WHERE trade_date=CURRENT_DATE" | awk '$1>=5{exit 0}{exit 1}'
grep -rn "print(" backend/collector/ && exit 1 || echo "✅ 无裸 print"
curl -sf http://localhost:8000/health | jq -e '.code==200'

# ❌ 坏的信号：
pytest  # 0 个用例也算通过
make test  # 不知道到底测了什么
python -c "import foo"  # 只验证能 import
```

---

## 3. 阶段契约（Stage Contracts）

跨阶段依赖通过"契约"显式传递，agent 不得跨阶段猜测接口。下表是权威契约，S0X 的 `输入依赖` 和 `输出产物` 必须与此一致：

| 生产者 | 消费者 | 契约内容 |
|--------|--------|----------|
| S01 → S02 | `backend/db/models.py` 中的 `LhbRecord`, `LimitUpPool`, `MarketSentiment` ORM；表 `lhb_records` / `limit_up_pool` / `market_sentiment` 至少有 60 交易日数据 |
| S02 → S03 | `backend/app/services/strategy/` 下的 `PhaseA.run()`, `PhaseB.run()`；`backend/app/services/trading/executor.py` 的 `open_position` / `close_position`；表 `orders`, `positions`, `daily_nav` |
| S03 → S04 | `http://localhost:8000/openapi.json` 可访问；所有 `/api/v1/*` 接口通过 `curl` 手测返回 `code:200` 包装；WebSocket `/ws/realtime` 连接成功并定时 ping |
| S04 → S05 | `frontend/dist/` 产物存在；`npm run build` 无警告；Nginx 能静态托管 |
| S05 → S06 | `docker-compose up -d` 起全栈；`./stop.sh`/`./start.sh` 可用；生产部署脚本 `deploy.sh` 幂等 |

**消费阶段启动前必须跑的契约自检：**

```bash
# S02 启动前
psql $DATABASE_URL -c "\dt" | grep -E "lhb_records|limit_up_pool|market_sentiment" | wc -l | awk '$1==3{exit 0}{exit 1}'
python -c "from backend.db.models import LhbRecord, LimitUpPool, MarketSentiment"

# S03 启动前
python -c "from backend.app.services.strategy.phase_a import PhaseA; from backend.app.services.strategy.phase_b import PhaseB"

# S04 启动前
curl -sf http://localhost:8000/openapi.json | jq -e '.paths | length > 10'
```

---

## 4. 禁止事项（所有阶段通用）

这些是**可机器检测**的通用红线，除 S0X 自己的禁止事项外一律适用：

| 规则 | 检测命令 |
|------|---------|
| 不得用 `print` 做日志 | `grep -rn "print(" backend/ --include="*.py" \| grep -v "^backend/tests"` |
| 不得用 `FLOAT/DOUBLE` 存金额 | `grep -rniE "(FLOAT\|DOUBLE).*amount" backend/db/` |
| 不得硬编码数据库连接串 | `grep -rnE "postgres(ql)?://[a-z]+:[^@]+@" backend/ --include="*.py"` |
| 不得 commit `.env` / 凭证 | `git ls-files \| grep -E "^\.env$\|credentials\|secret.*\.json$"` |
| 不得提交 `TODO`/`FIXME` 到完成阶段 | `grep -rn "TODO\|FIXME" backend/app/services/` |

---

## 5. 执行流程（每阶段按顺序）

```
① 读 _COMMON.md（本文件）
② 读 SXX_*.md 的「前置阅读」
③ 跑「阶段契约自检」（§3）——失败直接停，回到前一阶段
④ 按「具体任务」实现
⑤ 跑「完成信号」（SXX 的 §验收标准）——任一失败修到通过
⑥ 跑「通用禁止事项」自检（§4）
⑦ 执行 P0 部署闭环（§1）
⑧ 在 PROGRESS.md 登记：阶段名、完成时间、完成信号截图/输出
⑨ 停下等人类放行，**不要自动进入下一阶段**
```

第 ⑨ 步是硬性要求——金融项目错误会复利，跨阶段必须人工确认。

---

## 6. 进度登记（`docs/prompts/PROGRESS.md`）

完成每阶段后，agent 追加一条：

```markdown
## S0X — <阶段名>
- 完成时间：<ISO8601>
- 验收输出：<贴完成信号的 stdout>
- 部署验证：<curl 返回码 + 时间戳>
- 遗留问题：<可选，列未处理项>
```

下次对话 agent 先读 PROGRESS.md 确定从哪里继续。
