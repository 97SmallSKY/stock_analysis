# S05: 基础设施开发

---

## 1. Role & Boundary

**你负责**：Docker Compose、CI/CD、Makefile、监控告警、日志配置

**你不负责**：应用代码 → S01-S04

---

## 2. Tech Stack

- Docker 24+
- Docker Compose 2.20+
- Prometheus + Grafana
- GitHub Actions / GitLab CI

---

## 3. Directory

```
├── docker-compose.yml
├── docker-compose.prod.yml
├── Makefile
├── .env.example
├── scripts/
└── monitoring/
```

---

## 4. Deliverables

- docker-compose.yml（PostgreSQL + Redis）
- Makefile（一键启动/停止/状态）
- Prometheus + Grafana 配置
- 告警规则

---

## 5. Related Documents

- [DEPLOYMENT.md](../lhb-quant-v2/DEPLOYMENT.md)
- [RUNBOOK.md](../lhb-quant-v2/RUNBOOK.md)
