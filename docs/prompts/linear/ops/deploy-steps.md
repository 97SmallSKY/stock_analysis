# Ops 子文档 · 部署完整 bash + 回滚

> 父文档:[../ops.md](../ops.md)

---

## 📋 部署前必做清单

- [ ] 确认 PR 已 Approved
- [ ] 确认 Linear issue 有 Test 验收评论
- [ ] issue 挂 `test-passed` 标签
- [ ] 读 PR 描述「需要 Ops 注意的地方」
- [ ] 如有 schema 迁移 → 备份 DB(`pg_dump > backups/pre-STO-X-$(date +%s).sql`)
- [ ] 如有新依赖 → 确认 `requirements.txt` / `package.json` 已更新
- [ ] 如影响 scheduler → 准备重启命令
- [ ] 如影响 nginx → `nginx -t` 预检

---

## 🔀 JIT Merge 部署完整 bash(v3)

```bash
# ============ Step 1: 前置检查 ============
# 前置条件必须全满足:
#  - Linear issue 挂 test-passed 标签(Test 已签字)
#  - Linear issue 挂 @ops 标签(球在 Ops)
#  - PR 未合并(base=new-branch, head=<type>/STO-X-<slug>)

git fetch --all --prune
git checkout new-branch
git pull

# Dev 分支领先的 commits
git log --oneline origin/new-branch..origin/<type>/STO-X-<slug>
# new-branch 领先的(应为空,空=可 FF)
git log --oneline origin/<type>/STO-X-<slug>..origin/new-branch

# ============ Step 2: JIT Merge(FF-only) ============
git merge --ff-only origin/<type>/STO-X-<slug>

# ⚠️ 如果 FF 失败(new-branch 已前进):
#   git reset --hard origin/new-branch  # 放弃本次合并
#   # 评论 @dev 请 rebase 并重 push:
#   # `@dev new-branch 已前进,无法 FF-only merge。请:
#   #    git fetch && git rebase origin/new-branch && git push --force-with-lease`
#   # 等 Dev rebase 完再重跑本流程

# ============ Step 3: 推到 GitHub + 部署 jd ============
git push origin new-branch
make deploy
# 内部做:ssh jd + git pull + scripts/deploy.sh

# 若 systemd unit 未装(见 STO-10),手动拉起:
ssh jd 'pkill -f uvicorn; cd ~/stock_analysis && nohup .venv/bin/uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 > logs/api.log 2>&1 &'

# ============ Step 4: 健康检查 + 冒烟 ============
ssh jd 'systemctl status stock_analysis_api 2>/dev/null || ps -ef | grep uvicorn | grep -v grep'
bash scripts/smoke_http.sh  # 11 端点全 200 才算过

# ============ Step 5: 观察 5 分钟 ============
ssh jd 'tail -f logs/api.log'   # 无 ERROR / CRITICAL / 500 stack

# ============ Step 6: 删除 Dev 分支(部署成功才做) ============
gh pr merge <N> --merge  # GitHub 上合并 PR(同步 PR 状态)
git push origin --delete <type>/STO-X-<slug>  # 删远程分支
git branch -d <type>/STO-X-<slug>              # 删本地分支(如果有)

# ============ Step 7: Linear 归档 ============
# save_comment 加部署指纹(ops/templates.md)
# save_issue 转 Done,移除 @ops 标签
```

---

## 🚨 部署失败回滚流程(v3)

```bash
# 场景:Step 4/5 发现 smoke 失败或日志 ERROR

# ============ Step 1: 回滚代码 ============
ssh jd 'cd ~/stock_analysis && git reset --hard HEAD~1 && git log -1 --oneline'

# 本地也回滚 new-branch 到合并前
git reset --hard HEAD~1
git push --force-with-lease origin new-branch  # ⚠️ 强推前确认没别人基于新 head 工作

# ============ Step 2: 重新部署稳定版 ============
ssh jd 'cd ~/stock_analysis && ./scripts/deploy.sh'
bash scripts/smoke_http.sh  # 确认回到稳定版,全绿

# ============ Step 3: Linear 打回 Dev ============
# - 移除 test-passed 标签(实际没通过)
# - 移除 @ops 标签,加 @dev 标签
# - 状态 In Review → In Progress
# - save_comment 列具体失败证据(ops/templates.md 回滚记录模板)

# ⚠️ Dev 分支仍在 GitHub(删除是部署成功才做),等 Dev 修好再继续
```

---

## ⚠️ 部署时的 SG 代理陷阱

`backend/core/startup.py` 已自动清代理,但启动前再确认:

```bash
ssh jd 'env | grep -i proxy'  # 应该为空
```

若有残留,手动清:
```bash
ssh jd 'unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY'
```

---

## 🏃 手动拉起命令(systemd unit 未装时)

```bash
ssh jd '
  pkill -f uvicorn
  sleep 2
  cd ~/stock_analysis
  nohup .venv/bin/uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 > logs/api.log 2>&1 &
  sleep 3
  ps -ef | grep uvicorn | grep -v grep
'
```
