# daily_update 自动回填 STO-38 巡检 comment(STO-78)

> 背景:STO-38 daily 巡检 2 日内 2 次漏跑(STO-71/77 连续 AUDIT 告警)→ 证实「靠 Ops 主动扫日志」的设计必然漏。本方案用 systemd OnSuccess/OnFailure hook **自动**在 Linear STO-38 留 comment。

## 组件

| 文件 | 作用 |
|---|---|
| `scripts/linear_comment.sh` | 通用 Linear commentCreate 封装,参数 `<issue-id> <body>` |
| `scripts/daily_update_notify.sh` | 从 `logs/daily_update.log` 抓 end 行 + tail,构造 body 调 linear_comment.sh |
| `deploy/systemd/stock_analysis_daily_notify.service` | oneshot unit,被 daily.service OnSuccess/OnFailure 触发 |
| `deploy/systemd/stock_analysis_daily.service` | 已加 `OnSuccess=` / `OnFailure=` 挂钩 |

## 部署

### 1. 在 jd 的 `/etc/default/stock_analysis` 里注入 Linear API Token

```bash
# /etc/default/stock_analysis(root:root 0600)
LINEAR_API_TOKEN=lin_api_xxxxxxxxxxxx
```

Token 发放:Linear Settings → API → Personal API Key,标记用途「sto-38 auto notify」。**不要入 git**(.env 和 /etc/default/* 都在 .gitignore 里)。

### 2. 重新安装 systemd unit

```bash
ssh jd 'cd /root/stock_analysis && sudo bash scripts/install_units.sh'
# 或手动:
ssh jd 'sudo cp /root/stock_analysis/deploy/systemd/stock_analysis_daily*.service /etc/systemd/system/ && sudo systemctl daemon-reload'
```

### 3. 手动触发测试

```bash
ssh jd 'sudo systemctl start stock_analysis_daily.service'
# 等主 service 跑完(可能 ~1h),OnSuccess 会自动触发 notify
ssh jd 'sudo systemctl status stock_analysis_daily_notify.service'
# 日志:
ssh jd 'sudo journalctl -t stock_daily_notify --since "10 min ago"'
```

### 4. 验证 Linear 收到

刷新 STO-38 页面,应该看到新 comment(格式示例):

```
2026-04-23 19:37 CST ✅ daily_update success(systemd hook · STO-78)

[2026-04-23 19:37:33] ################ daily_update END errors=0 elapsed=3444s ################

<details>last 5 log lines 折叠</details>
```

## 失败语义(STO-78 AC · hook 失败不能影响主业务)

- `linear_comment.sh` **永远 exit 0**,curl 失败 / 无 token / 任何异常都只打 warning 到 journal,不传染给 daily 主任务
- `daily_update_notify.service` 设 `SuccessExitStatus=0 1 2`,容忍非 0 退出
- 万一 hook 本身 crash,systemd 最多记一行 failed,不触发递归告警(OnFailure 不指向自己)

## 故障排查

| 现象 | 排查 |
|---|---|
| daily.service 跑完 STO-38 无新 comment | `journalctl -t stock_daily_notify --since "1h ago"`,看 curl 有无 401/403 |
| curl 401 Unauthorized | `/etc/default/stock_analysis` 的 `LINEAR_API_TOKEN` 过期 / 未注入 |
| comment 身体乱码 | `daily_update.sh` 写日志用 utf-8,`linear_comment.sh` 用 python3 json.dumps 转义,应无问题;若出现查 `tail -5 logs/daily_update.log` 是否有特殊字符 |

## 相关 issue

- STO-38 · Watch daily 巡检(被保护对象)
- STO-71 / STO-77 · AUDIT(漏跑告警 2 次,触发本方案)
- STO-78 · 本 issue
