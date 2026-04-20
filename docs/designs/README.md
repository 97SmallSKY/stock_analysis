# `docs/designs/` —— 复杂任务总体设计

> 放什么:**开发周期 > 1 天** 或 **跨 3+ 模块** 的任务的总体规划文档。

## 命名规范

```
YYYY-MM-DD-<slug>.md
```

例:`2026-04-20-kline-range-selector.md`

## 内容模板

每份设计文档应包含:

1. **总体目标**(一句话说清楚想干嘛)
2. **影响范围**(哪些模块 / DB 表 / API / 前端页面)
3. **任务拆分**(一个可勾选的子任务 TaskList,每项带产出物)
4. **风险与回滚方案**
5. **完成判据**(什么样算做完,通常包含"部署 + 冒烟 + 日志观察")

## 与 TaskList 的关系

- 设计文档是**静态蓝图**,TaskList 是**动态执行记录**
- 每个设计的子任务都应该在 TaskList 登记一条
- 设计文档里的子任务**状态需要和 TaskList 保持一致**(勾选框)

## 完成后归档

全部子任务勾完后,把文件移到 `completed/` 子目录:
```bash
mkdir -p completed
mv YYYY-MM-DD-<slug>.md completed/
```

或在标题前加 `[DONE]` 标记(二选一)。

## 约定:与 `docs/IMPLEMENTATION_ROADMAP.md` 的关系

- `IMPLEMENTATION_ROADMAP.md` 是**历史周次计划**(W1~W12),已部分偏离实际
- `docs/designs/` 是**当前在推进的设计**,新增任务请写这里
- 两者冲突以 `docs/designs/` 为准
