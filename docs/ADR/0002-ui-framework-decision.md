# ADR 0002: UI 框架选型 —— 维持 Ant Design Vue + AG Grid, 不迁 Element Plus

状态: Accepted (2026-04-19, P7)
相关决策者: 开发组

---

## 背景

用户在 P7 阶段提出疑问: "element ui 是否更适合量化股票" (element.eleme.cn)。
已有 8 张页面全量基于 Ant Design Vue (AntdV) + AG Grid, 是否应当迁移到 Element Plus?

## 决策

**维持 Ant Design Vue v4 + AG Grid Community. 不迁 Element Plus.**

## 理由

### 1. 量化场景的真正瓶颈不是 UI 库, 是表格引擎

- 量化系统的核心交互 = 密集表格 (龙虎榜、持仓、回测历史、席位) + 大数据量虚拟滚动 + 列冻结 / 分组 / 聚合 / 导出 / 可拖拽列序。
- Element 的 `el-table`、AntdV 的 `a-table` 在 10k+ 行渲染、多列冻结、floatingFilter、列菜单等能力上**都不如 AG Grid**。
- 所以决定性选择是 **AG Grid**, 而不是 Element 还是 Antd。AG Grid 与 AntdV / Element 都兼容。

### 2. 切 UI 库的成本 = 重写所有页面, 收益 ≈ 0

现存组件依赖 AntdV:
- `a-modal / a-tabs / a-form / a-range-picker / a-slider / a-input-number / a-select /
   a-space / a-tag / a-empty / a-descriptions / a-card / a-menu / a-layout /
   a-table / a-button / a-pagination / a-spin / a-drawer / message / Empty.PRESENTED_IMAGE_SIMPLE`

迁 Element Plus 意味着对 8 个页面 + 1 个全局 Modal 做 1:1 组件替换与 CSS 重调, 没有任何功能上的增益。

### 3. AntdV 在金融类场景已是成熟解

- AntdV v4 是 Vue3 原生实现, 与 Element Plus 同为一等公民。
- Descriptions、Statistic、Card、Empty、Drawer、Tabs、FloatButton 这些"看板 / 报表"组件更贴金融场景, 文档与 API 更规整。
- Element 的长项在 "后台表单系统" (OA / CRM), 对图表 / 看板 / 数字密集场景没有额外收益。

### 4. 已在 P7 用最小代价补齐量化需求

P7 交付:
- `pages/LhbList.vue`: AG Grid 启用 `sortable + filter + floatingFilter` 默认列设置, ts_code 列 cellRenderer 点击触发全局 Drill Modal。
- `components/StockLink.vue`: 在 AntdV `a-table` 里做 ts_code 的可点击展示, 统一触发 `useStockDrill().open(code)`。
- `components/StockDrillModal.vue`: 日 K → 周 K (MA5) → 分钟 K 三级下钻, 使用 ECharts CandlestickChart 按需注册。
- 表格维度: Signals / LhbPnl / Robots / Backtest.history 全部列加 `sorter`, 满足 "所有表头都可排序筛选"。

**结果**: 未换 UI 库, 已实现用户目标(交互能力), 避免了重写成本。

## 回滚条件

若满足以下任一条件, 可重新评估迁 Element Plus:
- AntdV 停止维护或长期不更新 (当前 v4.x 活跃)
- 引入的第三方业务组件仅提供 Element 版本 (当前无)
- 设计规范强制要求 Element DLS

不满足则维持现状。

## 结论对比表

| 维度 | AntdV + AG Grid (保留) | Element Plus (候选) |
|---|---|---|
| 已有 8 页组件改动 | 0 | 全部重写 |
| 表格能力(量化核心) | ★★★★★ (AG Grid) | ★★★ (el-table) |
| Vue3 兼容 | ✅ | ✅ |
| 维护活跃度 | ✅ | ✅ |
| 金融看板组件 | ★★★★ | ★★★ |
| 学习曲线 | 已付 | 重新付 |
| 迁移收益 | — | 约等于 0 |
