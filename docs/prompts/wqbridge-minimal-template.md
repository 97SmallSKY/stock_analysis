# wqbridge 项目 · AI 最小协作规约(模板)

> **使用说明(给用户)**:
>
> 本文件是为 `~/PycharmProjects/Wanqing_Bridge` 仓库准备的**最小 Linear 协作规约模板**。
>
> 本仓库(stock_analysis)与 wqbridge **共用同一个 Linear team `Personal Dev`**,共享 STO 编号池。
> 若未来在 wqbridge 目录下运行 Claude Code 处理 Linear issue,**必须先部署本规约**,否则会跨项目串台。
>
> **怎么用**:
> 1. 复制本文件到 `~/PycharmProjects/Wanqing_Bridge/docs/prompts/linear-minimal.md`
> 2. 在 wqbridge 的 CLAUDE.md 第 1 节开头加一段:「任何涉及 Linear 的操作,先读 `docs/prompts/linear-minimal.md`」
> 3. 启动 Claude Code 时,开场白把本文件 @ 进来

---

## 🎯 项目锁定(最关键的 1 件事)

```
仓库:        ~/PycharmProjects/Wanqing_Bridge
Workspace:   xiaotian-dev
Team:        Personal Dev (前缀 STO-)
Project:     wqbridge
Project ID:  37ef915e-3363-47d8-8fa7-73f0202871da   ← 记住这个
```

> ⚠️ `Personal Dev` team 下**有多个 project**,它们**共用 STO 编号池**(STO-14 是 wqbridge 的,STO-43 是龙虎榜的,混编不连续)。**不带 project 参数查 issue 会串台**。

---

## 🔒 4 条强制规则

### 1. `list_issues` 必传 `project` 参数

```python
# ✅ 正确
list_issues(project="37ef915e-3363-47d8-8fa7-73f0202871da", state="Backlog")

# ❌ 错误(会拉到龙虎榜的 issue)
list_issues(state="Backlog")
```

### 2. `save_issue` 新建时必传 `project` 参数

```python
# ✅ 正确
save_issue(
    project="37ef915e-3363-47d8-8fa7-73f0202871da",
    title="修复 tool_repair bug",
    description="...",
    labels=["Bug"],
)
```

### 3. 按 ID 操作(`save_issue(id="STO-X", ...)`)前必先 `get_issue` 验证

```python
info = get_issue(id="STO-X")
if info.projectId != "37ef915e-3363-47d8-8fa7-73f0202871da":
    save_comment(id="STO-X", body="⚠️ 此 issue 属于其他 project,本仓库 AI 拒绝操作")
    return  # 跳过,处理下一条
# 验证通过才操作
save_issue(id="STO-X", ...)
```

### 4. 发现跨项目 issue 立即停手

不改状态、不提 PR、不 checkout 远程分支,仅留一条说明评论后跳过。

---

## 🤫 零打扰铁律(挂机模式)

**禁止调用 `AskUserQuestion`**。任何需要用户决策的场景:

1. 标题加 🔴 前缀
2. 打 `Urgent` 标签
3. 评论列清选项 + 默认推荐
4. 跳过此 issue,继续下一条

用户通过 Linear filter `label = Urgent` 或搜 `🔴` 批量查看等决策事项。

---

## 🧪 接手新会话时的自检(强烈推荐)

```python
# 扫本项目 In Progress,验证 projectId 全部一致
issues = list_issues(
    project="37ef915e-3363-47d8-8fa7-73f0202871da",
    state="In Progress",
)
for i in issues:
    assert i.projectId == "37ef915e-3363-47d8-8fa7-73f0202871da", f"跨项目污染!{i.id}"
print(f"✅ 共 {len(issues)} 条 In Progress,全部属于 wqbridge")
```

---

## 📚 相关文档(可选)

- 龙虎榜项目的完整规约:`~/Develop/personal/stock_analysis/docs/prompts/linear/`
  - 如果 wqbridge 未来也要引入 PM/Dev/Test/Ops 四角色模型,可参考那套
- 本规约是 v3.2 最小版,仅覆盖**多项目隔离 + 零打扰**两个铁律

---

## 📝 变更日志

- **2026-04-20 v3.2.minimal**:首版,对应主仓库规约 v3.2,仅为 wqbridge 提供最小防串台保护
