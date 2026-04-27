# Linear State Machine · 真实映射

> 本文件的门槛和转换规则详见 [transition-checklist.md](./transition-checklist.md)，这里只维护 state 名字映射。

## Team / Project

- Team：`Personal Dev` (key `STO`, id `a6a9d55b-9937-4e65-83bd-ac09d20a18e4`)
- Project：`龙虎榜量化交易分析系统` (id `5c09b8da-a3fc-45be-b866-5dc94cf01523`)
- Agent 共用 user id：`c3e854de-8659-4585-ae17-692a91e402d3`（xiaotian zhang）

## 5 槽 → Linear state 映射

| 语义槽 | Linear state 名 | state id | type | 当前 owner |
|---|---|---|---|---|
| Intake | `Backlog` | `560205f1-351a-4938-a0bc-bf549d3e88ae` | backlog | 人工 → PM |
| Ready for Dev | `Todo` | `16364983-96de-469e-91d5-dcfe6ba8f514` | unstarted | PM → Dev |
| In Progress | `In Progress` | `cd68c20f-f6b7-4dde-aeac-cf171503e5f6` | started | Dev |
| Ready to Deploy | `In Review` | `4bb1e0c1-5f34-4ee7-bbb3-a98b1fc94943` | started | Dev → Ops |
| Done | `Done` | `84365c14-55a8-49c4-8c98-12ee4c03764f` | completed | Ops |

## 非主线 state（不参与 T1~T6 流转，仅用于终态归档）

| Linear state | id | type | 用途 |
|---|---|---|---|
| `Canceled` | `9f1c208f-25d5-4756-b95a-2f6e9c9ef09e` | canceled | 需求取消 — PM 决策，加 `blocked:need-human` 后由人工拍板 |
| `Duplicate` | `9332d6aa-db06-459c-ba16-e514fe275203` | canceled | 与其他 issue 重复 — PM 在 comment 指明 duplicate-of |

## 标签清单

### 核心 6 个（transition-checklist.md T1-T5 直接引用）

| Label | id | 用途 |
|---|---|---|
| `auto:stock-analysis` | `da0ee563-176c-4806-ac09-44f03bd68373` | 项目开关，agent 只处理带此 label 的 issue |
| `type:feature` | `3cda0595-c173-494a-949d-ea5ab3ed0b23` | 新特性 → Dev 写 CHANGELOG.md |
| `type:bug` | `24d60901-25bc-4352-8232-2cbed9465e35` | bug → Dev 写 CHANGELOG.md |
| `type:chore` | `8e26c8fb-f266-4495-a652-fe4ef47dec26` | 重构/文档/基建 → Dev 写 DEVLOG.md |
| `type:watch` | `d4f62119-a3a0-483b-a269-c763c734ec69` | Watch 巡检类 issue,永久 In Progress,Auditor M8 豁免 |
| `blocked:need-human` | `bcc3949f-71d1-4af3-99ec-76bdf2da7a31` | agent 卡死升级人工(红色紧急,火灾) |

### 扩展 4 个（2026-04-25 新增）

| Label | id | 用途 |
|---|---|---|
| `needs:human-decision` | `03bfdd9a-9828-464f-824e-20f044d7d979` | 正常流程等用户拍板的非紧急决策(粉色,不紧急);见 CLAUDE.local.md §6 场景 |
| `domain:lhb` | `65e034a8-3c92-4fe0-9dcf-96164fd4b85e` | 龙虎榜业务域 issue,便于按域切片统计/路由 |
| `verified:human` | `5e9714ac-da28-4289-966f-ac2c1e71ec51` | 人工已验证通过,与自动化 test-passed 区分 |
| `type:data-bug` | `41c677f4-d29f-4706-b29d-a994b86d5231` | 数据质量 bug(数据源错/字段单位/缺失行),Dev 需查 ingest/domain 层 |

## Bootstrap 结果

- 2026-04-21：6 核心 label 到位,5 语义槽 + 2 终态归档映射完成
- 2026-04-25：补 4 扩展 label(needs:human-decision / domain:lhb / verified:human / type:data-bug)

---

**维护**：若 Linear workflow state 增删改，PM 下轮循环发现漂移后立即更新本文件并在 sentinel issue 留 comment。
