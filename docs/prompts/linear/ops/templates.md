# Ops 子文档 · 模板集

> 父文档:[../ops.md](../ops.md)

---

## 🚀 部署指纹评论模板(转 Done 时用)

```markdown
✅ 已部署到 jd · 三方共识完成

## 🔀 JIT Merge
- **源分支**:`<type>/STO-X-<slug>` @ `<source-SHA>`
- **合并方式**:`git merge --ff-only`
- **new-branch 新 head**:`<new-head-SHA>`
- **Dev 分支状态**:✅ 已删除(GitHub 远程 + 本地)

## 🚀 部署
- **时间**:<ISO 8601>
- **make deploy**:成功
- **smoke**:11/11 端点 200(详细贴日志)
- **进程**:新 PID `<xxx>` / 旧 PID `<yyy>` 已停
- **systemctl status**:active (running)

## 👀 观察 5 分钟
- 日志无 ERROR / CRITICAL
- 关键指标:<如 DB 连接数 / API 响应延时,可选>

## 📊 影响范围
- 后端 / 前端 / schema:<三项哪些改动>
- 长任务 Watch:<有 / 无,Watch issue STO-Y>
- 用户可见改动:<是 / 否,已更新 CHANGELOG>

**转 Done ✅**
```

---

## 🚨 事故 Bug issue 模板(建时)

```markdown
## 现象

<用户看到什么 / 系统表现什么>

## 复现步骤

1. ...
2. ...

## 期望 vs 实际

- 期望:...
- 实际:...

## 影响范围

- 用户:<受影响用户 / 请求数>
- 数据:<是否有数据污染,污染范围>
- 服务:<是否不可用 / 降级>

## 初步定位

<日志片段 / 堆栈 / 怀疑模块>

## 紧急处置

- [ ] 是否已回滚?(commit <SHA>)
- [ ] 是否已隔离?(关闭某端点/降级某模块)

## 验收

- [ ] 复现步骤已无法触发问题
- [ ] 加回归测试(开 Test 侧 issue 关联)
- [ ] 更新 ADR(如涉及决策)
- [ ] 更新 DEVLOG(技术笔记)
```

---

## 🔔 Watch 巡检 issue 模板(建时)

```markdown
## 任务描述

<这个长任务在干什么:如「每日 17:30 拉取龙虎榜数据入库」>

## 运行规律

- 触发:<cron / systemd timer / 手动>
- 预期耗时:<X 分钟 ~ Y 小时>
- 成功标志:<DB 表行数 / 日志关键词 / API 指标>

## 巡检清单(每日/每周)

- [ ] 日志无 ERROR/CRITICAL
- [ ] DB 对应表有新增记录 N 条以上
- [ ] 进程/服务 active(running)
- [ ] 无熔断器长时间 OPEN

## 异常处置 runbook

- 现象 A → 处置 A
- 现象 B → 处置 B

## 历史巡检记录

<!-- 每次巡检追加:YYYY-MM-DD ✅ 指标 A=X / B=Y -->
```

---

## 🔄 回滚记录评论模板

```markdown
🚨 **Ops 部署失败,已回滚 + 打回 Dev**

- 合并 commit: `<SHA>`(已 reset)
- 回滚到:`<prev-SHA>`
- 失败证据:
  - smoke 失败端点:`GET /api/xxx` 500
  - 日志片段:<贴错误堆栈>

- 原 `test-passed` 已撤销
- @dev 请修复后重提 PR(原 Dev 分支仍在,修完继续)
```

---

## 📢 Watch 巡检常规评论模板

### 正常
```markdown
<YYYY-MM-DD HH:MM> ✅ 指标 A=X / B=Y / 无异常
```

### 异常
```markdown
<YYYY-MM-DD HH:MM> ⚠️ 发现异常:<描述>
已建 Bug issue STO-Y 跟进
```
