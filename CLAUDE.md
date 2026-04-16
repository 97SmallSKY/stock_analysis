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
