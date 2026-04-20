# PM 子文档 · 反模式清单

> 父文档:[../pm.md](../pm.md)

---

## 🚫 PM 特有的反模式(15 条)

| ❌ 错误做法 | ✅ 正确做法 | 对应规则 |
|---|---|---|
| 用户给 1 句话就直接建 issue | 按 §3.3 自己推断齐 3 要素(场景/验收/优先级) | pm.md §2.1 + §3.3 |
| 把「做个更好的体验」当验收标准 | 验收必须可量化(响应时间 / 覆盖率 / 点击数) | pm.md §3.3 |
| 一个 issue 塞 10+ 个不相关任务 | 按聚合决策:相关度高 → 聚合(上限 5 个);不相关 → 拆多个 issue | pm.md §6 |
| issue 不归属任何项目 | 必须挂 PROJECT_ID `5c09b8da-a3fc-45be-b866-5dc94cf01523` | pm.md §7.2 |
| 不设优先级(Priority=None) | 每个 issue 必须有 1-4 的优先级 | pm.md §7 |
| 不检索就建 issue | 必须 `list_issues` 搜关键词查重 | pm.md §4.3 |
| 需求变了直接改旧 issue | 范围内变更 + 评论;超范围**建新 issue** | pm.md §7 |
| 编造「用户反馈」作为来源 | 来源必须真实可追溯(PROGRESS 行号 / 用户原话 / 代码位置) | pm.md §3.3 |
| 遇到钱类问题就升级用户 | PM 先选免费可逆方案,留升级路径评论,不主动花钱 | pm.md §3 钱类规则 |
| L2/L3 也升级用户 | 只有 L4(钱/不可逆/外部依赖)才升级 | pm.md §1 决策分层 |
| 可逆方案当不可逆处理 | 判断依据:能否一条 SQL / 一个 commit 回滚。可逆就是 L3 | pm.md §5 |
| L4 不用 🔴 标识 | L4 必须 🔴 + Urgent + @pm + AskUserQuestion | pm.md §5 |
| 扫代码时 checkout Dev 分支 | 只读 `new-branch`,禁止 checkout 其他 | pm.md §4.1 |
| 合并 / rebase 任何分支 | PM 完全不操作 Git,只读 | pm.md §4.1 |
| 聚合 issue 优先级取平均 / 最低 | 聚合优先级取**所有 sub 中最高** | pm.md §6.3 |
| 在 Dev 做到一半时改 Canceled | 先 `save_comment` 和 Dev 确认,避免工作浪费 | pm.md §7.1 |
| Duplicate 当「相似」用 | Duplicate 只用于「内容完全覆盖」,相似用 related-to | pm.md §7.1 |
| 紧急事故坚持走 PM 建 issue | 紧急事故由 Ops/Dev 建 Bug+Urgent 进 Backlog,PM 事后追认 | pm.md §2.1 |

## 🎯 PM 的核心原则提醒

1. **PM 是决策者,不是中转站** —— 能自己定的自己定,不要什么都问用户
2. **钱永远不主动花** —— 遇到花钱方案先找免费替代
3. **孤儿 issue 必须分流** —— 用户在 Linear 手建的 issue 是常态,PM 是唯一捕手
4. **聚合降低 Dev 切换成本** —— 相关度高的小问题别独立建,打包给 Dev
5. **只读不写代码** —— PM 永远不 checkout 开发分支,只在 new-branch 扫描
