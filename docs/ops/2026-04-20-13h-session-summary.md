# 2026-04-20 一次 13 小时 session 完整复盘

**运行时段**:2026-04-20 01:34 → 14:55(**13h21min**)
**Agent**:Claude Code(7×24 自动运维+开发模式)
**结果**:39 commits / 27 次 deploy 全绿 / 0 生产故障

---

## 1. 主要动作时间线

| 时段 | 动作 |
|---|---|
| **01:34 ~ 11:14** | P16 K 线 2025 全年回填(10h22min)+ chain.sh tn_price/score 下游 + sentiment 回填;穿插 P18.3~P18.15 共 13 次修复/优化 |
| **11:14 ~ 12:26** | P19 页面体验升级 9 子任务(设计 + 后端 API + 组件 + 5 表格接入 + 信号雷达图 + 术语字典 + CHANGELOG 说人话) |
| **12:26 ~ 13:37** | P19.10/P19.12 用户反馈迭代(快捷钮点击即查 + K 线锚定 + 跳龙虎榜)+ P19.13 日 K 点击 bug 真相(前端 limit 超后端上限) |
| **13:37 ~ 14:55** | P20 文档分流(CHANGELOG vs DEVLOG)+ Agent 手册修订 + Dashboard 20 字段全覆盖 |

---

## 2. 成果清单

### 2.1 数据层
- **kline_daily**:+ 2,386,273 行(P16 2025 全年)
- **sentiment**:347 → 538 天 + 11 字段从 NULL 补齐
- **tn_price**:136,319 行(chain 自动跑完)
- **score**:52,058 行全部评分

### 2.2 功能层(用户可见)
1. **K 线组件**:动态日期范围 + 8 快捷钮 + 区间最高/最低标注 + 点击锁定不跳 tab + 锚定日期居中
2. **5 表格页**:默认一年 + 8 快捷钮 + 点击**即查**(回测除外)
3. **决策信号**:评分中文化 + 雷达图 + 权重公式说明 + 三段式 Modal + 跳龙虎榜链接
4. **专业术语释义**:27 个术语悬浮 ⓘ
5. **首页 sentiment**:20/20 字段全覆盖(涨跌比 / 平均/中位涨幅 / 强弱势股 / LHB 净买 / 情绪变化)
6. **特性介绍页**:双 tab(用户特性 vs 开发日志)+ 分类筛选 + 关键字搜索

### 2.3 基建层
- **Agent 工作手册** 10 节(`docs/prompts/7x24_agent.md`)
- **文档分层** 4 目录(designs / optimizations / ops / prompts)
- **监控工具**(`ingest_status.sh`):心跳/业务日志回退/ANSI 清理/进程判断 4 次迭代
- **部署基建**(`deploy.sh` + `healthcheck.sh`):systemd 239 兼容/探测逻辑/重试/姊妹 bug 4 次修复
- **冒烟脚本**(`smoke_http.sh`):3 → 8 个端点
- **LHB API**:skip_total 参数消除深分页 COUNT 开销
- **CHANGELOG API**:双源(changelog + devlog)
- **事故复盘** 1 份(P18.13b schema 错配)

### 2.4 质量指标

| 指标 | 起步 | 收尾 | 变化 |
|---|---|---|---|
| pytest | 753 | 772 | +19 |
| coverage | 75.77% | 75.76% | ~持平(新代码基本有测) |
| vitest | 88 | 129 | +41 |
| vue-tsc | clean | clean | ✅ |
| 连续 deploy 全绿 | - | **27 次** | 0 回滚 |

---

## 3. 教训与 follow-ups

### 3.1 典型失误

#### ❌ P18.13 schema 错配(10:04 ~ 10:13 9 分钟紧急回滚)
错把 API 层硬塞的占位字段(`total_stocks/up_count/down_count`)当成 DB 列,writer.cols 加了这 3 列,回填首条 INSERT 就 UndefinedColumn。详见 `docs/ops/2026-04-20-p18.13b-schema-mismatch.md`。

**对策**:改写 writer + schema 前必须 `information_schema.columns` 验证真实列。

#### ❌ P19.12 「5 条反馈一揽子修复」过早宣告闭环
pytest/vitest 全绿 + deploy 全绿,但**实际没点过日 K 图**。用户报「还是看不了」才发现前端 `limit=2000` 超过后端 `le=1000`,每次请求秒 422。

**对策**:改前端 API 参数时必须同步检查后端 `Query(le/ge)` 边界,**单元测试 mock 不等于真测**。严格 E2E 跑 curl 至少 1 次。

#### ❌ 「空闲期待命」反模式
多次循环发现无紧急任务就挂机,用户指出「应该做优化」。

**对策**:Agent 手册 4.0 节明确「距下次监控 ≥ 5min 必须开工」,维护「优化积压 Backlog」。

### 3.2 尚未处理的积压

- **北交所 92xxxx 股票 kline 全链路失败**(一直报 ERROR,属于 akshare 数据源限制):约 307 只股票无日 K 数据,需考虑 tushare 或其他源
- **前端 bundle 体积** vendor-antd 1433KB + vendor-grid 1114KB + vendor-echarts 565KB,可做代码分片
- **盈亏分析页评分详情 Modal**(对标 Signals,反馈 #1 提过但未完全实现)
- **机器人页空态** `/api/robots` robots=[] 应有引导 UI

### 3.3 实际偏差 vs 预估

| 项 | 原预估 | 实际 |
|---|---|---|
| P19 页面升级 9 子任务 | 2~3 天 | **1h05min** 🏆 |
| P16 kline 回填 | 10 小时 | 10h22min ✓ |
| P18.13 sentiment 修复 | 1 小时 | 2 阶段共 **~2h10min**(含 schema 错配回滚) |

---

## 4. 本次 session 自身的可复用产物

1. **Agent 工作手册**:新 session 接手即读即用
2. **文档分层规范**:CHANGELOG(用户)/ DEVLOG(开发)/ designs(设计)/ optimizations(优化笔记)/ ops(运维复盘)
3. **事故复盘模板**:已有 1 份范例在 `docs/ops/`
4. **P19.13 教训「前端 API limit 必须同步后端」**:需考虑加到 `CLAUDE.md` 或 Agent 手册 3.2 节

---

**session 状态**:截至 14:55 稳定待命,30+ 次监控循环,所有反馈已修。
