# PM 子文档 · Issue 模板集

> 父文档:[../pm.md](../pm.md)

---

## 📝 按类型选模板

### 1. Feature / Task(新功能或普通任务)—— PM 最常用

```markdown
## 背景

<为什么要做:业务场景 / 痛点 / 来源>

## 任务范围

- [ ] 步骤 1
- [ ] 步骤 2
- [ ] 步骤 3

## 来源

<用户原话 / PROGRESS.md 某行 / 依赖 issue STO-Y>

## 验收

- 可量化指标 1(如:API 返回 < 200ms)
- 可量化指标 2(如:前端通过 vitest + vue-tsc)
- 若是长任务:必须配套 Watch issue(标题 `[Watch] XXX 日常巡检`)
```

### 2. Improvement / Refactor(优化或重构)

```markdown
## 现状

<当前代码/行为是什么样>

## 问题

<为什么要改>

## 目标

<改成什么样>

## 任务范围

- [ ] ...

## 验收

- 行为不变(对比测试通过)
- 新指标满足 <XXX>
```

### 3. Docs(文档类)

```markdown
## 现状

<哪份文档过时 / 缺失>

## 目标

<要补 / 更新什么>

## 任务范围

- [ ] 更新 `docs/XXX.md`
- [ ] 同步 README 引用
- [ ] 新增 ADR(如涉及决策)

## 验收

- 新开发者能按此文档跑通 X 流程
```

### 4. 聚合父 issue(v3 新增)

```markdown
## 背景

PM 巡检 <date> 发现 <模块> 有 N 处相关问题/优化,聚合处理以降低 Dev 切换成本。

## 子任务(N 项,请一起完成)

- STO-<id1>: 子任务 1 简述
- STO-<id2>: 子任务 2 简述
- STO-<id3>: 子任务 3 简述

## 总体验收

- [ ] N 个 sub-issue 的验收标准全部达成
- [ ] 新增测试覆盖 N 个场景
- [ ] smoke 端点不受影响

## 分支命名建议

feat/STO-<父id>-<slug>

## 来源

- PM 巡检 <date> @ new-branch `<SHA>`
```

### 5. 聚合 issue 的代码(save_issue 批量建)

```python
# Step 1: 建父 issue
parent = mcp_linear.save_issue(
    title="[聚合] <主题>",
    team=TEAM_ID, project=PROJECT_ID,
    priority=max(t.priority for t in subtasks),  # 取最高优
    labels=["Improvement", "@dev"],
    description="<按上方模板 4 填>"
)

# Step 2: 建每个 sub-issue,parentId 指向父
for task in subtasks:
    mcp_linear.save_issue(
        title=task.title,
        team=TEAM_ID, project=PROJECT_ID,
        priority=task.priority,
        parentId=parent.id,   # ← 关键字段
        labels=[task.type_label],  # 不挂 @dev(父已挂)
        description=task.description
    )
```

---

## 决策评论模板(留痕 · L2/L3)

```markdown
🧭 **PM 决策(v2 规则)**
- **结论**:<一句话>
- **理由**:<1-3 条,引用规则原文>
- **备选**:<其他方案及劣势>

### 角色标签
- 移除 `@pm` → 加 `@<role>`

—— PM 批处理(YYYY-MM-DD)
```

## 升级评论模板(L4 · 🔴)

```markdown
⏸️ **PM 决策 · 需升级用户(L4)**
- **类别**:💰 钱 / ⚠️ 不可逆 / 🔗 外部依赖(选一)
- **待你回答**:<具体 Yes/No 或 A/B/C 问题>
- **PM 推荐**:<默认会选哪个,若用户不回>
- **超时默认**:否(需要用户明确回应)

请用户回复后移除 🔴 标记 + Urgent 标签 + @pm 标签。
```

## 孤儿 issue 捕获评论

```markdown
🧭 **PM 捕获孤儿 issue**

此 issue 原无角色标签,PM 巡检时按规约分流:
- 判定:L<X>
- 处置:<自己定 / 升级 / 给 Dev>
- 角色标签:<已打的标签>

—— PM 巡检(YYYY-MM-DD HH:MM)
```
