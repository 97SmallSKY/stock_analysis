#!/bin/bash
# ============================================================
# 龙虎榜策略分析系统 - 启动脚本
# ============================================================

# 脚本所在目录（兼容从任意目录调用）
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PID_FILE="$DIR/.streamlit.pid"
LOG_FILE="$DIR/logs/streamlit.log"
PORT="${STREAMLIT_PORT:-8501}"

# 检查是否已在运行
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "⚠️  程序已在运行 (PID=$OLD_PID)，访问 http://localhost:$PORT"
        echo "   若要重启，请先执行 ./stop.sh"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# 创建日志目录
mkdir -p "$DIR/logs"

# 自动找到安装了 streamlit 的 Python
# 优先 .venv，其次 miniconda3（服务器），再次 anaconda，最后系统 python3
if [ -f "$DIR/.venv/bin/python" ] && "$DIR/.venv/bin/python" -c "import streamlit" &>/dev/null; then
    PYTHON="$DIR/.venv/bin/python"
elif [ -f "/root/miniconda3/bin/python" ] && /root/miniconda3/bin/python -c "import streamlit" &>/dev/null; then
    PYTHON="/root/miniconda3/bin/python"
elif [ -f "/opt/homebrew/anaconda3/bin/python" ] && /opt/homebrew/anaconda3/bin/python -c "import streamlit" &>/dev/null; then
    PYTHON="/opt/homebrew/anaconda3/bin/python"
elif command -v python3 &>/dev/null && python3 -c "import streamlit" &>/dev/null; then
    PYTHON="$(command -v python3)"
else
    echo "❌ 找不到安装了 streamlit 的 Python，请先执行: pip install streamlit"
    exit 1
fi

echo "============================================================"
echo "  龙虎榜策略分析系统"
echo "============================================================"
echo "  Python  : $PYTHON"
echo "  目录    : $DIR"
echo "  端口    : $PORT"
echo "  日志    : $LOG_FILE"
echo "============================================================"

# 启动 Streamlit（后台运行）
nohup "$PYTHON" -m streamlit run "$DIR/app.py" \
    --server.port "$PORT" \
    --server.address "0.0.0.0" \
    --server.headless true \
    --browser.gatherUsageStats false \
    >> "$LOG_FILE" 2>&1 &

APP_PID=$!
echo "$APP_PID" > "$PID_FILE"

# 等待启动
echo ""
echo "⏳ 等待服务启动..."
for i in $(seq 1 15); do
    sleep 1
    if curl -s "http://localhost:$PORT/_stcore/health" >/dev/null 2>&1; then
        echo ""
        echo "✅ 启动成功！(PID=$APP_PID)"
        echo ""
        echo "   本机访问: http://localhost:$PORT"
        echo "   局域网  : http://$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
        echo ""
        echo "   停止程序: ./stop.sh"
        echo "   查看日志: tail -f $LOG_FILE"
        exit 0
    fi
    echo -n "."
done

echo ""
echo "⚠️  启动超时，请检查日志: tail -20 $LOG_FILE"
exit 1
