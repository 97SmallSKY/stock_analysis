# P19 · 页面体验大升级

**日期**:2026-04-20
**负责人**:Claude Code Agent(7×24)
**预计周期**:2~3 天(分 7 个子任务独立部署)

---

## 1. 总体目标

基于用户 2026-04-20 反馈,**系统性重做前端页面体验**:

- K 线组件:时间范围动态化、默认 6 个月、快捷按钮、高低价标注
- 信号/表格:日期范围默认一年、快捷选择、专业术语释义
- 决策信号:评分明细中文化 + 多维图下钻
- 特性介绍:文案改白话,去专业术语

**成功标准**:
- 所有页面表格的日期范围有统一的「1Y / 6M / 3M / 1M / 1W / 5D / 今天」快捷按钮
- K 线默认显示近 6 个月数据,提供「1Y / 6M / 3M / 1M / 2W / 1W / 5D / 最新」快捷按钮
- K 线图上可视化标注区间最高价 / 最低价
- 决策信号评分详情展示中文名称 + 可下钻雷达图
- 页面上所有专业术语有 ⓘ 悬浮/点击提示
- 特性介绍页现有旧技术化条目被逐步改写为说人话

---

## 2. 影响范围

| 模块 | 受影响文件/组件 |
|---|---|
| 后端 API | `backend/api/kline.py`(加 `GET /api/kline/daterange/{ts_code}` 返回可用日期范围) |
| 前端 K 线 | `frontend/src/components/KlineChart.vue` / `StockDrillModal.vue` / 相关的时间范围 state |
| 前端表格 | `frontend/src/pages/LhbList.vue` / `Signals.vue` / `LhbPnl.vue` / `Backtest.vue` / `Reports.vue` |
| 前端共用 | 新建 `frontend/src/components/DateRangeQuickPick.vue`(统一日期快捷选择) |
| 前端共用 | 新建 `frontend/src/components/TermTooltip.vue`(专业术语悬浮释义) |
| 前端共用 | 新建 `frontend/src/utils/glossary.ts`(术语字典) |
| 信号页 | `frontend/src/pages/Signals.vue` 评分明细中文化 + 雷达图下钻 |
| 特性介绍 | `docs/CHANGELOG.md` 历史技术化条目改写(可分批) |

---

## 3. 任务拆分

按「单点小改」原则,每个子任务独立成 1 个 commit + 1 次 deploy 闭环。**不互相阻塞**,可任意顺序执行。

### P19.1 · 后端 K 线可用日期范围 API

**产出**:
- `GET /api/kline/daterange?ts_code=xxx&freq=daily|weekly|minute` → `{"min": "2020-01-01", "max": "2026-04-17"}`
- 后端单测 ≥ 2
- **同时修 P2 任务 #16**(healthcheck.sh 姊妹 bug,顺手)

**完成判据**:
- pytest 绿
- jd 部署冒烟:`curl /api/kline/daterange?ts_code=600519.SH` 返回正确日期区间

### P19.2 · 前端 K 线时间范围动态化 + 默认 6 个月

**依赖**:P19.1

**产出**:
- `KlineChart.vue` 初始化时先调 `daterange` API,按 max 日期**回推 6 个月**作为默认区间
- 日期选择器的 min/max 限制为该股票的实际数据范围(不再硬编码 `2026-01-20`)

**完成判据**:
- 打开任一股票抽屉,默认看到 6 个月 K 线
- 日期选择器不能选超过数据范围的日期

### P19.3 · 前端 K 线快捷按钮(1Y / 6M / 3M / 1M / 2W / 1W / 5D / 最新)

**产出**:
- `KlineChart.vue` 顶部加按钮组替换/补充底部的 dataZoom 横滑
- 点击「6M」立即跳到最近 6 个月,以此类推
- 「最新」= 最近 60 个交易日

**完成判据**:
- 8 个按钮切换丝滑(< 200ms 重绘)
- 选中态高亮

### P19.4 · K 线图高低价标注

**产出**:
- ECharts `markPoint` 在当前显示区间的最高价/最低价处打标签
- 颜色:最高价红 ▲,最低价绿 ▼,带数值

**完成判据**:
- 视觉可见,不遮挡蜡烛图

### P19.5 · 共用组件:日期范围快捷选择器 `DateRangeQuickPick.vue`

**产出**:
- 一个可复用的 Vue 组件,暴露 props `:modelValue` / `:max-date`,events `@change`
- 快捷按钮:1Y / 6M / 3M / 1M / 1W / 5D / 今天
- **默认**:开始 = 一年前,结束 = 今天
- **语义**:用户点快捷钮,只改**开始日期**,结束日期 = 今天(或保持用户已选结束)
- 配套 `utils/date_range.ts` + 单测 ≥ 5

**完成判据**:
- vitest 绿
- Storybook 式的单独调试页可跑通(可选)

### P19.6 · 接入 5 个表格页:默认一年 + 快捷选择

**依赖**:P19.5

**产出**(分 5 个独立 commit,每页 1 个):
- [ ] P19.6a · LhbList.vue
- [ ] P19.6b · Signals.vue
- [ ] P19.6c · LhbPnl.vue
- [ ] P19.6d · Backtest.vue
- [ ] P19.6e · Reports.vue

**完成判据**(每页):
- 默认显示近一年数据
- 快捷按钮工作
- 不改变现有 API 调用契约

### P19.7 · 共用组件:专业术语 Tooltip `TermTooltip.vue` + 术语字典

**产出**:
- `frontend/src/utils/glossary.ts` 初版覆盖 **20+ 术语**:
  - 评分维度:净买入额、胜率、换手率、振幅、PE-TTM、PB、...
  - 龙虎榜:机构买入、游资、一线游资、龙头、...
  - 情绪:涨停强度、封板率、连板、炸板、...
  - 回测:夏普、最大回撤、年化、...
- `TermTooltip.vue`:`<TermTooltip term="hot_money">游资</TermTooltip>` 悬浮显示术语解释
- Signals 页 / Dashboard / LhbList 里的专业名词改用此组件

**完成判据**:
- 鼠标悬浮 500ms 触发提示
- 点击 ⓘ 图标也可触发(移动端)
- glossary.ts 有单测覆盖查找逻辑

### P19.8 · Signals 评分明细中文化 + 雷达图下钻

**依赖**:P19.7(复用术语字典)

**产出**:
- 评分明细字段名从 `buy_amount / hot_money_net / ...` 改为「买入额 / 游资净买入 / ...」
- 点击某行「详情」→ 弹出雷达图 Modal,展示该条记录的各维度得分
- 复用 ECharts radar chart

**完成判据**:
- 中文字段 100% 覆盖
- 雷达图交互正常

### P19.9 · CHANGELOG 历史条目批量说人话改写

**产出**:
- 把 CHANGELOG 里 P18.3 ~ P18.14 共 12 条技术化条目,改写为「现象 / 原因 / 修复 / 效果」四段式白话版
- 保留原标题日期(不改 git 历史),只改正文

**完成判据**:
- 前端 `/changelog` 页看起来不含文件路径、函数名、commit 代号
- 用户凭正文能看懂每次修了什么

---

## 4. 风险与回滚

| 风险 | 缓解 |
|---|---|
| K 线 API 响应变慢(daterange 额外一次查询) | 加 query plan 验证 + 缓存 15s |
| 共用组件接入时改坏现有表格 | 每个 P19.6.x 独立 commit,可单独 revert |
| ECharts markPoint 遮挡蜡烛 | 用 `symbol: 'pin'` + `symbolOffset` 调整 |
| 术语字典覆盖不全 | TermTooltip 兜底「暂无解释」提示,收集 user feedback 逐步补 |

---

## 5. 完成判据(整体)

- [x] 设计文档已写(本文件)
- [ ] P19.1 后端 API + #16
- [ ] P19.2 K 线动态范围
- [ ] P19.3 K 线快捷按钮
- [ ] P19.4 K 线高低价
- [ ] P19.5 DateRangeQuickPick 组件
- [ ] P19.6a~e 5 个表格接入
- [ ] P19.7 TermTooltip + glossary
- [ ] P19.8 信号评分中文化
- [ ] P19.9 CHANGELOG 说人话改写
- [ ] 全部 P19.x 部署到 jd 并冒烟通过
- [ ] 整体体检 `/api/dashboard` + 5 个前端页面

**完成后**:本文件标题加 `[DONE]` 或移至 `docs/designs/completed/`。

---

## 6. 执行顺序建议

```
今日(2026-04-20):
  1. 先做 P19.1(后端 API + 姊妹 bug #16)→ 单次闭环
  2. 做 P19.5(DateRangeQuickPick 组件 + 单测)→ 单次闭环
  3. 做 P19.7(TermTooltip + glossary 骨架)→ 单次闭环

明日:
  4. P19.2 / P19.3 / P19.4(K 线三连改)→ 3 次独立闭环
  5. P19.6a~e(5 次接入)→ 5 次独立闭环
  6. P19.8(评分中文化 + 雷达图)→ 单次闭环

后续空闲期:
  7. P19.9(CHANGELOG 批量改写)→ 单次闭环
```

---

**最后更新:2026-04-20**
