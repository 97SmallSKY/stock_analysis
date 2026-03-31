#!/bin/bash
# Streamlit 启动/保活脚本 (修复版 2026-03-30)
# 修复: pgrep -f 需要引号; PID 写入用变量而非 echo $!
PID_FILE=~/stock_analysis/.streamlit.pid
LOG_FILE=~/stock_analysis/logs/streamlit.log
PYTHON=/root/miniconda3/bin/python
PORT=8051

# 精确匹配进程（引号是关键——不加引号时 run/app.py 被 bash 当成 pgrep 的额外参数被忽略）
RUNNING_PID=$(pgrep -f "streamlit run app.py" | head -1)

if [ -n "$RUNNING_PID" ]; then
    # 服务进程实际在运行，刷新 PID 文件后静默退出
    echo "$RUNNING_PID" > "$PID_FILE"
    exit 0
fi

# 进程不存在，清理旧 PID 文件并重启
rm -f "$PID_FILE"
mkdir -p ~/stock_analysis/logs
cd ~/stock_analysis

nohup $PYTHON -m streamlit run app.py \
    --server.port $PORT \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false \
    >> "$LOG_FILE" 2>&1 &

NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"
echo "Streamlit started on port $PORT, PID=$NEW_PID"
