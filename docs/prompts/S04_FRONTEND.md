# S04: 前端开发

---

## 1. Role & Boundary

**你负责**：Vue3 页面、组件、状态管理、API 集成、WebSocket 集成

**你不负责**：后端 API → S03；业务逻辑 → S02

---

## 2. Tech Stack

- Vue 3 + TypeScript
- Vite
- Ant Design Vue
- Pinia
- ECharts / LightweightCharts
- AG-Grid

---

## 3. Directory

```
frontend/src/
├── views/
│   ├── Dashboard.vue
│   ├── RobotList.vue
│   ├── RobotDetail.vue
│   └── ...
├── components/
│   ├── common/
│   └── charts/
├── stores/
├── api/
└── composables/
```

---

## 4. Deliverables

### 4.1 页面功能

详见 `docs/lhb-quant-v2/API.md` 的前端需求部分

### 4.2 WebSocket 集成

- 自动重连
- 心跳保活
- 频道订阅管理

---

## 5. Related Documents

- [API.md](../lhb-quant-v2/API.md)
