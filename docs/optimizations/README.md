# `docs/optimizations/` —— 单点优化技术笔记

> 放什么:**单次 commit 就能搞定** 的小型性能 / 可读性 / 健壮性优化笔记。
>
> 如果一个优化需要多个 commit / 跨多模块 → 用 `docs/designs/` 而不是这里。

## 命名规范

```
YYYY-MM-DD-<slug>.md
```

例:
- `2026-04-20-healthcheck-retry.md`
- `2026-04-20-lhb-skip-total.md`

## 内容模板

半技术半白话,面向**本项目的后续开发者**(不是终端用户):

```markdown
# <优化标题>

**日期**:YYYY-MM-DD
**commit**:<hash>
**影响文件**:`path/to/file.py` 等

## 现象

<一两句话描述原有行为的问题>

## 根因

<代码/SQL/配置层面的原因分析>

## 修复

<具体做了什么改动,最好带代码片段>

## 效果验证

<压测数据 / 日志对比 / 冒烟截图>

## 备注

<未来可能的扩展 / 相关 issue>
```

## 与 CHANGELOG.md 的区别

| | CHANGELOG.md | optimizations/ |
|---|---|---|
| 受众 | 终端用户 | 开发者 |
| 语言 | 说人话 | 可用技术术语 |
| 颗粒度 | 一条摘要 | 完整分析 |
| 条目类型 | 混合(feat/fix/perf) | 仅 perf / refactor |
