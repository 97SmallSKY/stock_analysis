# 2026-05-02 4-Agent 系统深层架构与认识论批判

> 结论：之前的 `plan-review` 和 `governance` 仅仅是在“修补 Markdown 文档的官僚流程”，而这份批判真正指出了该系统在**分布式系统工程**和**AI 认识论**上的致命缺陷。如果不解决这些根本问题，4-Agent 方案在实际上线的第一天就会因为并发冲突、状态漂移或幻觉闭环而全线崩溃。

## 核心被验证的深层问题

### 1. 分布式系统设计的缺失（物理与逻辑崩溃点）
- **并发与 Git 冲突**：4 个 Agent 在独立的 worktree 中高频读写共享文档（如 `CHANGELOG.md`, `PROGRESS.md`），这必然导致非 fast-forward 冲突。系统将因为纯粹的 Git 冲突而陷入死锁，而非业务逻辑错误。
- **状态漂移与无版本绑定**：Linear 的状态（In Review）与代码库的真实状态（Git SHA）是断裂的。Ops Agent 极有可能在验证一个尚未 Push 完成、或者已经被覆盖的旧 Commit（“测试幽灵”）。
- **非幂等性（Idempotency）**：Bootstrap 缺乏资源唯一性校验，重启会导致重复建 Label、建 Issue 和覆盖核心配置。

### 2. AI 认识论的陷阱（逻辑闭环与翻译误差）
- **AI 审计 AI 的死局**：整个系统缺乏真正的“外部绝对真理”。Dev 写出来的代码和保护测试，由 Ops 运行自己决定的 Playwright 断言来验证，最后由 Auditor（另一个 AI）来审查。这只是“幻觉的一致性”，而非业务的正确性。
- **意图的自然语言降级**：从 PM 的 AC，到 Dev 的代码，再到 Ops 的验证，每一次交接都在进行有损的自然语言翻译。这种累积误差在当前系统内**完全不可见且不可测**。
- **PM 并非 Architecture Owner**：AI 缺乏全局上下文驻留能力。让 PM Agent 仅凭文档片段就去框定“代码级边界”，纯属类别错误（Category Error）。

### 3. “悬空”的基础设施（纸上谈兵的治理）
- **不存在的 M12-M16 工具链**：Auditor 被规定为“只读脚本报告”，但系统中根本没有任何角色（无论是 Dev 还是 CI）被指派去编写和运行这些静态分析脚本。治理沦为了一纸空文。
- **独立的单点 Bail-out**：单个 Agent 失败挂起（`blocked:need-human`），但没有全局共享的 Watch Channel（总线），导致系统处于“局部罢工、全局仍在瞎转”的撕裂状态。

## 破局建议（Next Steps）

1. **引入强状态绑定**：废弃纯文本的 `@Ops ready` 交接，所有的状态流转必须强绑定具体的 Git Commit SHA，Ops 只针对特定 SHA 拉取和测试。
2. **将规则转化为可执行代码**：废弃靠 Agent 记忆的 `state-machine.md` 规则，引入 `verify_labels.py`、`check_ast_complexity.py` 等硬性脚本。凡是能用代码校验的，绝对不让 LLM 去阅读理解。
3. **串行化或细粒度锁（Locking）**：在文档读写上引入严格的文件锁，或将 4-Agent 降级为单线程多角色切换模型，彻底根除多 Worktree 带来的 Git 并发灾难。
