#!/bin/bash
# ============================================================
# 龙虎榜策略分析系统 - 停止脚本
# ============================================================

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/.streamlit.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "ℹ️  未发现 PID 文件，尝试通过端口查找进程..."
    PORT="${STREAMLIT_PORT:-8501}"
    PIDS=$(lsof -ti ":$PORT" 2>/dev/null)
    if [ -z "$PIDS" ]; then
        echo "ℹ️  程序未运行"
        exit 0
    fi
    echo "🔍 发现占用 $PORT 端口的进程: $PIDS"
    kill $PIDS 2>/dev/null && echo "✅ 已停止 (PID=$PIDS)" || echo "❌ 停止失败"
    exit 0
fi

PID=$(cat "$PID_FILE")

if kill -0 "$PID" 2>/dev/null; then
    echo "🛑 停止服务 (PID=$PID)..."
    kill "$PID"
    # 等待进程退出
    for i in $(seq 1 10); do
        sleep 1
        if ! kill -0 "$PID" 2>/dev/null; then
            echo "✅ 已停止"
            rm -f "$PID_FILE"
            exit 0
        fi
    done
    # 强制终止
    echo "⚠️  进程未响应，强制终止..."
    kill -9 "$PID" 2>/dev/null
    echo "✅ 已强制停止"
else
    echo "ℹ️  进程 $PID 已不存在"
fi

rm -f "$PID_FILE"
