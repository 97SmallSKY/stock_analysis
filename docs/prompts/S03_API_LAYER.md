# S03: API 层开发

---

## 1. Role & Boundary

**你负责**：FastAPI 路由、Pydantic Schema、WebSocket、中间件

**你不负责**：业务逻辑 → S02；数据层 → S01；前端 → S04

---

## 2. Tech Stack

- FastAPI 0.109+
- Pydantic v2
- WebSocket
- slowapi（限流）

---

## 3. Directory

```
backend/app/api/
├── v1/
│   ├── router.py
│   ├── robots.py
│   ├── trades.py
│   ├── market.py
│   ├── selection.py
│   ├── dashboard.py
│   └── audit.py
└── ws/
    └── connection.py
```

---

## 4. Deliverables

### 4.1 核心 API 端点

详见 `docs/lhb-quant-v2/API.md`

### 4.2 WebSocket 协议

- subscribe/unsubscribe/ping/pong
- seq + timestamp
- 频道：robot.{id}.trades, robot.{id}.positions, market.lsb

---

## 5. Related Documents

- [API.md](../lhb-quant-v2/API.md)
