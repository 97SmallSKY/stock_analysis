# Test 子文档 · 验收清单 + 写测试 5 原则

> 父文档:[../test.md](../test.md)

---

## 🔍 验收 Dev PR 的 16 项清单

审 Dev PR 时,按以下 4 大类 16 项逐一核对。**有一条命中 = Request changes**。

### 文档合规 4 项(不过直接打回,不浪费本地跑测时间)

- [ ] PR 描述按 dev/templates.md 模板填齐?
- [ ] 「自测结果」4 层勾选?
- [ ] 「issue 验收项勾选情况」填了?
- [ ] 「影响范围」写清了?

### 测试验证 5 项(本地真跑一遍)

- [ ] `git checkout <dev-branch>` 后 `pytest -q` 全绿?
- [ ] 覆盖率不降?(对比 new-branch 基线)
- [ ] `vue-tsc --noEmit` 0 error?
- [ ] `vitest run` 全绿?
- [ ] 新增代码有对应测试?(看 diff 里 `+` 代码是否都有配套 `_test.py` 或 `.test.ts`)

### 业务验证 3 项(对照 issue)

- [ ] issue 任务范围 checklist 打勾是否真实?(抽 1-2 项手动验)
- [ ] issue 验收标准是否可测量达标?
- [ ] 有无 range creep(做了 issue 之外的东西)?

### 代码质量 4 项(看 diff)

- [ ] 无 `print(` / `console.log`?
- [ ] 无硬编码密钥 / 绝对路径?
- [ ] 变量命名清晰?
- [ ] 不违反 ADR(ORM / 跨分支)?

---

## ✍️ 写测试的 5 原则(主动补测时用)

1. **每个 test case 一个明确的「what」**(名字即描述):`test_scorer_handles_empty_input`
2. **Arrange-Act-Assert** 三段式,不要混在一起
3. **不要 mock 太深**:mock 到数据源接口这一层就停,不要 mock 业务逻辑
4. **边界值必测**:空 / 单行 / 重复 / null / 超大
5. **回归测试**:每次修 bug 必须加一个能复现这个 bug 的 case

---

## 📐 三层测试策略(主动补测时用)

按顺序从廉价到昂贵:

| 层 | 跑哪里 | 示例 | 本地能跑吗? |
|---|---|---|---|
| **单元** | 纯函数 / 边界 / 错误路径 | `tests/test_parser.py` | ✅ 本地 |
| **集成** | DB / 数据网关 / 缓存 | `tests/test_db_crud.py` / `tests/test_data_fetch.py` | ⚠️ 本地 IP 被封,**只在 jd 上跑** |
| **端到端** | 部署后 smoke script | `scripts/smoke_http.sh` | ⚠️ 需要真实服务(Test 不跑,交 Ops) |

**目标覆盖率**:
- 核心业务模块(`backend/domain/*` 的 scorer / aggregator / backtest):≥ 80%
- 整体项目基线:≥ 75%(pytest.ini 门槛)
- `backend/ingest/*` 已 `.coveragerc` omit(纯 adapter-IO),不计入

---

## 📁 测试文件命名与位置

```
tests/test_<module>.py          # 单元 / 集成测试,pytest 自动发现
tests/test_api_<endpoint>.py    # API 端点测试
frontend/src/<xxx>.test.ts      # 前端单测,vitest 自动发现
frontend/tests/e2e/*.spec.ts    # E2E(如用 Playwright)
```

---

## 📊 覆盖率检查标准操作

```bash
# 后端整体
python3 -m pytest --cov=backend --cov-report=term-missing -q

# 看特定模块
python3 -m pytest --cov=backend.domain.lhb.scorer --cov-report=term-missing tests/test_scorer.py -v

# 对比 PR 前后(在 Dev 分支上)
git log -1 --format='%h'                         # 记录 Dev 分支 SHA
python3 -m pytest --cov=backend -q 2>&1 | tail -5 # Dev 分支覆盖率
git checkout new-branch
python3 -m pytest --cov=backend -q 2>&1 | tail -5 # new-branch 基线
```
