# S06: 测试开发

---

## 1. Role & Boundary

**你负责**：单元测试、集成测试、E2E 测试、CI 集成

**你依赖**：S01-S04 的代码

---

## 2. Tech Stack

- pytest + pytest-asyncio + pytest-cov
- Playwright（E2E）
- testcontainers

---

## 3. Directory

```
backend/tests/
├── unit/
├── integration/
└── api/

frontend/tests/
├── e2e/
└── unit/
```

---

## 4. Deliverables

- 单元测试覆盖率 ≥ 80%
- 集成测试覆盖核心流程
- E2E 测试覆盖主要用户路径

---

## 5. Related Documents

- [TESTING.md](../lhb-quant-v2/TESTING.md)
