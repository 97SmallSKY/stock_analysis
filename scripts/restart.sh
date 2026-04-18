#!/usr/bin/env bash
# restart.sh — 不拉代码,只重启服务。用于配置热更新。
#
# 本项目只有 stock_analysis_api 一个常驻 unit(APScheduler 嵌在内),
# 历史的 stock_analysis_worker / stock_analysis_scheduler 已废弃(见 P5.2)。
# 前端由 FastAPI 自己 serve frontend/dist,不再需要 reload nginx(P5.1b)。
set -euo pipefail

svc=stock_analysis_api
if systemctl list-unit-files | grep -q "^${svc}.service"; then
    echo "==> Restarting $svc"
    sudo systemctl restart "$svc"
else
    echo "==> ${svc}.service 未安装,请先跑 scripts/install_units.sh" >&2
    exit 1
fi
echo "==> Restart complete"
