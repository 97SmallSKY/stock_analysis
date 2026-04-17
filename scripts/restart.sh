#!/usr/bin/env bash
# restart.sh — 不拉代码，只重启服务。用于配置热更新。
set -euo pipefail
for svc in stock_analysis_api stock_analysis_worker stock_analysis_scheduler; do
    if systemctl list-unit-files | grep -q "^${svc}.service"; then
        echo "==> Restarting $svc"
        sudo systemctl restart "$svc"
    fi
done
sudo systemctl is-active --quiet nginx && sudo systemctl reload nginx || true
echo "==> Restart complete"
