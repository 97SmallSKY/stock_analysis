# CLAUDE.md — Claude Code 行为规范

---

## ⚠️ P0 — 每次修改完成后必须 rsync 上传并重启服务

**这是最高优先级规范，每轮修改完成后必须执行，不得跳过：**

```bash
# Step 1：rsync 同步改动文件（远程服务器无 git 仓库，不能 git pull）
rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
  pages/xxx.py jd:~/stock_analysis/pages/
# 多目录改动时同步整个项目：
rsync -avz --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.git' --exclude='.claude/' --exclude='data/' --exclude='logs/' \
  ./ jd:~/stock_analysis/

# Step 2：重启 Streamlit 服务使改动生效
ssh jd "cd ~/stock_analysis && ./stop.sh && sleep 1 && STREAMLIT_PORT=8051 ./start.sh"
```

**关键信息（已验证）：**
- 服务器别名：`ssh jd`（京东云 VPS）
- 服务器项目路径：`~/stock_analysis`
- Python：`/root/miniconda3/bin/python`（系统 python3 无 streamlit）
- 启动脚本：`./start.sh`（已支持 miniconda 路径）
- 停止脚本：`./stop.sh`
- 线上访问地址：**http://117.72.184.8:8051**

**为什么是 P0：** 项目运行在远程服务器上，本地修改不会自动同步。不部署 = 用户永远看到旧版本。

---

## 项目说明

A 股龙虎榜量化交易分析系统，基于 Streamlit + PostgreSQL 构建。
详细业务规范见 `skill/总控 Prompt.md`，系统架构见 `AGENTS.md`。

---

## 🧠 Plan 拷问与决策沉淀（grill-with-docs）

本项目已注入 `grill-with-docs` skill（在 Hermes / Claude Code 通用 skill 库里）。

**何时触发**：
- 用户写完 plan、提出新设计、问"这个方案靠谱吗 / 帮我 review 思路"
- 多个概念在对话里反复打架（命名/边界不清）
- 要做架构改动前，先确认共识

**4 个动作**（一次只一题，每题给推荐答案，等用户回复）：
1. 挑战术语冲突（对照本项目 CONTEXT.md / 既有命名）
2. 锐化模糊语言（提议 canonical term）
3. 具体场景压测（造边界场景探概念）
4. 代码 vs 主张交叉验证（看代码是否真支持你说的）

**沉淀位置**（懒创建，写在本 git repo 根目录）：
- `CONTEXT.md` — 领域术语词典（一句话定义、_Avoid_ 同义词、Relationships、Flagged ambiguities）
- `docs/adr/0001-slug.md` — 架构决策记录，1-3 句话即可

**ADR 三必要条件**（缺一不写）：难逆转 + 无上下文会困惑 + 真存在 trade-off。Hermes 加一条：用户对同类决策反悔过 ≥ 1 次的话，强烈建议落 ADR。

**不要**：把通用编程概念（timeout/retry/cache）塞进 CONTEXT.md；给可逆/显然决策建 ADR。

详细规范见 `~/.hermes/skills/software-development/grill-with-docs/SKILL.md`。
