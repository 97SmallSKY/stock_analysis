# `docs/ops/` —— 运维手册 / 事故复盘

> 放什么:
> - 部署流程 / systemd 配置变更说明
> - 生产事故复盘
> - 监控告警规则
> - 外部依赖(akshare/tushare/PG/Redis)的坑点与应对

## 命名规范

```
YYYY-MM-DD-<slug>.md         # 事故复盘 / 单次运维事件
<topic>.md                    # 长期运维手册(不过期)
```

例:
- `2026-04-20-p16-kline-backfill-10h.md`(事件)
- `akshare-rate-limit-playbook.md`(手册)
- `systemd-units-guide.md`(手册)

## 事故复盘模板

```markdown
# <事件标题>

**日期**:YYYY-MM-DD HH:MM ~ HH:MM(持续 Xh Ymin)
**严重度**:P0 / P1 / P2
**影响**:<哪些功能被影响,影响多少用户>

## 时间线

| 时间 | 事件 |
|---|---|
| HH:MM | <发生/发现> |
| HH:MM | <定位> |
| HH:MM | <修复> |
| HH:MM | <恢复> |

## 根因

<深入到代码/配置/环境的最底层原因>

## 修复动作

1. 紧急止损:...
2. 彻底修复:commit <hash>

## 教训

- 流程层面:...
- 技术层面:...
- 监控层面:...

## Follow-ups

- [ ] 防止复发的 CHANGELOG 条目
- [ ] 新增告警规则
- [ ] 写进 ADR(如果涉及架构决策)
```

## 运维手册模板

手册类不带日期,持续维护。标题直接是主题,例如:

```markdown
# akshare 限流应对手册

## 限流触发条件
...

## 观测方式
...

## 应对策略
...
```
