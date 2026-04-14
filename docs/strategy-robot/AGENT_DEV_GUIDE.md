# 策略机器人平台 - Agent 开发指南

> 本文档用于指导 Agent 按阶段循环开发验证策略机器人平台
> 分支：`feature/strategy-robot-platform`

---

## 📋 开发阶段状态

| 阶段 | 文档 | 代码 | 验证 | 状态 | 完成时间 |
|------|------|------|------|------|----------|
| P0 | ✅ | ⬜ | ⬜ | ⬜ 未开始 | - |
| P1 | ✅ | ⬜ | ⬜ | ⬜ 未开始 | - |
| P2 | ✅ | ⬜ | ⬜ | ⬜ 未开始 | - |
| P3 | ✅ | ⬜ | ⬜ | ⬜ 未开始 | - |
| P4 | ✅ | ⬜ | ⬜ | ⬜ 未开始 | - |
| P5 | ✅ | ⬜ | ⬜ | ⬜ 未开始 | - |

### 状态说明
- ✅ 已完成
- 🔄 进行中
- ⬜ 未开始

---

## 🤖 Agent 开发 Prompt

```
# 角色
你是一个专业的股票量化开发工程师，负责按照 docs/strategy-robot/ 目录下的分阶段文档开发策略机器人平台。

# 项目信息
- 仓库: https://github.com/97SmallSKY/stock_analysis.git
- 本地目录: /Users/xiaotian/Develop/personal/stock_analysis
- 远程服务器: ssh jd → ~/stock_analysis
- 线上地址: http://117.72.184.8:8051

# 当前分支
feature/strategy-robot-platform

# 开发阶段顺序
P0 → P1 → P2 → P3 → P4 → P5（必须按顺序执行）

# 当前任务状态（由用户告知当前阶段）
当前阶段: [由用户指定，如 P0]
```

---

## 🔄 Agent 执行循环

### 循环一：读取阶段文档

```bash
# 读取当前阶段的完整文档
cat docs/strategy-robot/phase-[当前阶段]-*.md
```

### 循环二：检查数据库现状

```bash
ssh jd "cd ~/stock_analysis && /root/miniconda3/bin/python -c \"
from utils.storage import storage
from sqlalchemy import inspect
engine = storage._get_engine()
inspector = inspect(engine)
tables = inspector.get_table_names()
print('已有表:', tables)

# 检查当前阶段需要的表
required_tables = {
    'P0': ['sim_trades', 'sim_trade_events', 'sim_selection_log'],
    'P1': ['sim_robots', 'cash_ledger', 'strategy_registry'],
    'P2': ['sim_daily_summary', 'benchmark_daily'],
    'P3': ['score_weights_config'],
    'P4': ['market_snapshots', 'monitor_events'],
    'P5': ['elimination_records', 'elimination_schedule', 'dividend_records'],
}
for t in required_tables.get('[当前阶段]', []):
    cols = [c['name'] for c in inspector.get_columns(t)] if t in tables else []
    print(f'{t}: {cols}')
\"
```

### 循环三：执行数据库迁移

```bash
# 创建迁移脚本（如不存在）
cat > scripts/migrate_[当前阶段].py << 'EOF'
# 从文档中复制迁移代码
EOF

# 执行迁移
ssh jd "cd ~/stock_analysis && /root/miniconda3/bin/python scripts/migrate_[当前阶段].py"
```

### 循环四：实现核心代码

```bash
# 按文档创建核心模块
# P0: skills/stock_sim_robot/trade_cost.py, position_calc.py
# P1: skills/stock_sim_robot/cash_manager.py, robot_manager.py, strategy_base.py, strategy_loader.py
# P2: skills/stock_sim_robot/daily_settlement.py
# P3: skills/stock_sim_robot/scorer.py, charts.py
# P4: skills/stock_sim_robot/realtime_monitor.py
# P5: skills/stock_sim_robot/eliminator.py, dividend_handler.py, data_cleaner.py

# 同步到远程
rsync -avz --exclude='__pycache__' --exclude='*.pyc' ./ jd:~/stock_analysis/
```

### 循环五：运行验证脚本

```bash
# 创建验证脚本
cat > scripts/verify_[当前阶段].py << 'EOF'
# 从文档中复制验证代码
EOF

# 执行验证
ssh jd "cd ~/stock_analysis && /root/miniconda3/bin/python scripts/verify_[当前阶段].py"
```

### 循环六：测试前端页面

```bash
# 重启 Streamlit（如需要）
ssh jd "cd ~/stock_analysis && pkill -f 'streamlit run' && nohup streamlit run app.py --server.port 8051 > logs/streamlit.log 2>&1 &"

# 等待启动
sleep 3

# 检查日志
ssh jd "tail -20 ~/stock_analysis/logs/streamlit.log"
```

### 循环七：提交代码

```bash
git add [修改的文件]
git commit -m "feat: [当前阶段] 完成 - [简短描述]"
git push origin feature/strategy-robot-platform

# 更新本文档状态（手动标记）
# 将当前阶段状态从 ⬜ 改为 ✅
```

### 循环八：询问下一阶段

完成当前阶段后，Agent 应询问用户：
> "✅ P[当前阶段] 开发完成！
> 
> **完成情况：**
> - 数据库迁移：✅/❌
> - 核心代码：✅/❌
> - 验证通过：✅/❌
> 
> 是否继续 P[下一阶段]？"

---

## 📁 阶段文档索引

| 阶段 | 文档路径 | 核心任务 |
|------|----------|----------|
| P0 | [phase-P0-bugfix-and-cost.md](./phase-P0-bugfix-and-cost.md) | Bug修复 + 交易成本体系 |
| P1 | [phase-P1-robot-crud.md](./phase-P1-robot-crud.md) | 机器人CRUD + 策略注册 |
| P2 | [phase-P2-daily-nav.md](./phase-P2-daily-nav.md) | 每日净值 + 净值曲线 |
| P3 | [phase-P3-comparison-dashboard.md](./phase-P3-comparison-dashboard.md) | 对比看板 + 评分体系 |
| P4 | [phase-P4-realtime-monitor.md](./phase-P4-realtime-monitor.md) | 实时监控 + 盘中止损 |
| P5 | [phase-P5-elimination.md](./phase-P5-elimination.md) | 淘汰机制 + 分批建仓 |

---

## 🔧 常用开发命令

### 数据库检查
```bash
ssh jd "cd ~/stock_analysis && /root/miniconda3/bin/python -c \"
from utils.storage import storage
from sqlalchemy import inspect
i = inspect(storage._get_engine())
for t in i.get_table_names():
    print(t)
\"
```

### 查看表结构
```bash
ssh jd "cd ~/stock_analysis && /root/miniconda3/bin/python -c \"
from utils.storage import storage
from sqlalchemy import inspect
i = inspect(storage._get_engine())
cols = i.get_columns('[表名]')
for c in cols:
    print(f'{c[\\"name\\"]}: {c[\\"type\\"]}')
\"
```

### 查看日志
```bash
ssh jd "tail -100 ~/stock_analysis/logs/streamlit.log"
```

### 重启服务
```bash
ssh jd "cd ~/stock_analysis && ./stop.sh && ./start.sh"
```

### 代码同步
```bash
rsync -avz --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' ./ jd:~/stock_analysis/
```

### Git 操作
```bash
# 查看当前分支
git branch

# 查看提交历史
git log --oneline feature/strategy-robot-platform | head -10

# 查看未提交更改
git status

# 撤销最后一次提交（如需重做）
git revert HEAD
git push origin feature/strategy-robot-platform
```

---

## ⚠️ 开发注意事项

1. **必须按顺序开发**：每个阶段依赖前一阶段的成果
2. **验证通过后再继续**：确保每个阶段功能正确
3. **及时提交代码**：每个阶段完成后立即提交
4. **遇到问题先排查**：查看日志、检查数据库表结构
5. **复用现有代码**：如 `sim_trade_db.py`、`monitor_intraday.py` 中的功能

---

## 🚀 快速开始

### 方式一：指定阶段开发

用户告诉 Agent：
> "开始开发 P0"

Agent 执行 P0 阶段的完整开发流程，完成后询问是否继续。

### 方式二：批量开发

用户告诉 Agent：
> "请按顺序完成所有阶段（P0-P5）的开发，每完成一个阶段后询问我是否继续"

### 方式三：指定跳过已完成的阶段

用户告诉 Agent：
> "P0-P2 已完成，从 P3 开始开发"

Agent 检查 P0-P2 的成果，确认后从 P3 开始。

---

## 📊 开发进度追踪

### 当前状态
- 当前开发阶段：待开始
- 已完成阶段：0/6
- 进度：0%

### 完成记录

| 日期 | 阶段 | 完成内容 | 提交哈希 |
|------|------|----------|----------|
| - | - | - | - |

---

## 📞 技术支持

如遇问题，请检查：
1. 数据库连接：`ssh jd && cd ~/stock_analysis && python -c "from utils.storage import storage; print(storage._get_engine())"`
2. 依赖安装：`ssh jd "pip list | grep -E 'streamlit|sqlalchemy|psycopg2'"`
3. 日志文件：`ssh jd "tail -100 ~/stock_analysis/logs/streamlit.log"`
4. 进程状态：`ssh jd "ps aux | grep streamlit"`

---

*本文档由 Agent 自动生成，最后更新：2026-04-14*
