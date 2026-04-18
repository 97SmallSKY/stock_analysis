# W3 龙虎榜评分模型 — 设计文档

> 起草: 2026-04-18  
> 状态: 设计中 / 待 W2 灌库完成后验证

---

## 1. 目标

为 `lhb_stock_daily` 中每只上榜股票打出 **0-100 分综合评分**(`score`),
把评分拆解为 3 个维度存入 `score_tech / score_fund / score_hist`,
并在 `score_detail` (JSONB) 中保留每个子因子的打分,方便回溯与调参。

最终产出:
- `backend/scoring/lhb_score.py` — 评分模型代码
- `backend/cli.py score` — CLI 入口: `python -m backend.cli score --from 20240101 --to 20241231`
- 单元测试 `tests/test_lhb_score.py`

---

## 2. 数据来源

| 表 | 用途 |
|---|---|
| `lhb_stock_daily` | 本表, 主键 `(ts_code, trade_date)` |
| `kline_daily` | 技术面、历史走势 |
| `market_sentiment` | 大盘情绪修正系数 |
| `stock_info` | 行业、市值属性 |

---

## 3. 评分公式

```
score = score_tech × 0.40 + score_fund × 0.35 + score_hist × 0.25
```

### 3.1 技术面 `score_tech` (0-100)

反映**上榜当日及前 N 日**的技术强度。

| 因子 | 权重 | 逻辑 |
|---|---|---|
| 当日涨跌幅 | 0.30 | `pct_change` 绝对值越大分越高(涨停 >9.5% 给满分) |
| 近 5 日强度 | 0.25 | 过去 5 日累计涨幅, 高于 15% 给满分 |
| 成交量激增 | 0.20 | `vol / avg_vol_20` 比值(量比), >3 给满分 |
| 换手活跃度 | 0.15 | `turnover_rate`, 5%-30% 区间满分, 过高过低扣分 |
| 位置(距 60 日高点距离) | 0.10 | 越接近高点分越高 |

### 3.2 资金面 `score_fund` (0-100)

反映**龙虎榜当日资金流向**与**主力买入力度**。

| 因子 | 权重 | 逻辑 |
|---|---|---|
| 净买入金额 | 0.35 | `l_net` 绝对值, 取对数归一化 0-100 |
| 净买入占成交比 | 0.25 | `l_net / amount`, 5%-20% 给满分 |
| 买卖力度比 | 0.20 | `l_buy / (l_sell + 1)`, >2.5 给满分 |
| 市值适中性 | 0.10 | 流通市值 20-300 亿给满分, 过小过大扣分 |
| 上榜原因分级 | 0.10 | 连续三日涨幅累计 30% > 单日涨跌 > 换手 > 振幅 |

### 3.3 历史胜率 `score_hist` (0-100)

反映**该股过去上榜后**的**3/5/10 日**平均涨幅与胜率。

| 因子 | 权重 | 逻辑 |
|---|---|---|
| 过去 1 年上榜次数 | 0.20 | 3-8 次给满分(太多说明惯性炒作, 太少样本不足) |
| 上榜后 3 日平均涨幅 | 0.35 | >5% 给满分 |
| 上榜后 5 日平均涨幅 | 0.25 | >7% 给满分 |
| 上榜后 5 日胜率 | 0.20 | 胜率 >60% 给满分 |

---

## 4. 大盘情绪修正(可选)

最终 `score` 基础分算出后,乘以**大盘情绪系数** `k`:

| `sentiment_level` | k |
|---|---|
| HOT | 1.05 |
| WARM | 1.02 |
| NEUTRAL | 1.00 |
| COOL | 0.97 |
| COLD | 0.92 |

反映"熊市龙虎榜后续表现普遍偏弱"的经验事实。clamp 到 [0, 100]。

---

## 5. 实现框架

```python
# backend/scoring/lhb_score.py
@dataclass
class ScoreInput:
    lhb_row: dict       # lhb_stock_daily 一行
    kline_window: list  # 上榜日前 60 个交易日的 kline
    sentiment_level: str
    hist_stats: dict    # 预先聚合好的"过去1年同 ts_code"统计

def compute_tech_score(inp: ScoreInput) -> tuple[float, dict]: ...
def compute_fund_score(inp: ScoreInput) -> tuple[float, dict]: ...
def compute_hist_score(inp: ScoreInput) -> tuple[float, dict]: ...

def compute_score(inp: ScoreInput) -> dict:
    """返回 {score, score_tech, score_fund, score_hist, score_detail}"""
```

### CLI 入口

```bash
# 对 [start, end] 区间所有 lhb 行批量算分, 写入 score / score_* 字段
make score FROM=20240101 TO=20241231
python -m backend.cli score --from 20240101 --to 20241231

# 单股调试
python -m backend.cli score --code 000001.SZ --date 20240115
```

### 批量更新 SQL

```sql
UPDATE lhb_stock_daily
SET score = %s, score_tech = %s, score_fund = %s, score_hist = %s,
    score_detail = %s::jsonb, updated_at = NOW()
WHERE ts_code = %s AND trade_date = %s;
```

---

## 6. 数据口径约束(踩坑备忘)

- `kline_daily.pct_change` 是**小数形式** (0.0523 = 5.23%)。评分逻辑按小数写。
- `lhb_stock_daily.l_net / l_buy / l_sell` 单位是**元**(从 akshare 原样). 计算占比时分母是
  `amount`(千元/万元 请查 schema), 必须先对齐单位
- **空值处理**: `kline_window` 不满 60 天(新股)时, 技术面窗口因子降分 20%;
  历史上榜次数 = 0 时, `score_hist` 直接给 50 分(保守中性)

---

## 7. 测试策略

### 7.1 单元测试(不依赖 DB)

- 3 个维度的 pure-function 测试: 给定 ScoreInput, 断言输出范围和明细
- 边界: 涨停日、跌停日、新股(kline 不足)、ST 股、首次上榜(hist_stats 为空)

### 7.2 集成测试(依赖已灌好的 DB)

- 对 `000001.SZ` / `600519.SH` 等已知股票跑一次,打印评分分布
- 对比: 2024/10/08 涨停潮日(sentiment HOT) 的 score 分布 vs 普通交易日

### 7.3 烟雾测试(生产前)

```bash
python -m backend.cli score --from 20240101 --to 20240131
# 期望: 更新行数 > 0, 所有 score 在 [0, 100]
```

---

## 8. 验收标准

1. `backend/scoring/lhb_score.py` 三个维度函数 + 主函数 + 批量入库
2. `backend/cli.py` 新增 `score` 子命令
3. 18111 行 2024 年 lhb 数据 100% 被打分, 耗时 < 2 分钟
4. 单元测试覆盖率 > 80%
5. `score` 分布合理: HOT 日均值 > NEUTRAL 日均值 > COLD 日均值

---

## 9. 后续(W4+)

- **回测**: 每天选 score top 10, 模拟 T+3/T+5 持有, 看累计收益
- **策略装配**: 不同 `reason_type` 用不同权重组合 → 策略矩阵
- **FastAPI 只读接口**: `GET /api/lhb/top?date=&limit=` / `GET /api/lhb/{ts_code}/history`
- **调参 Notebook**: `notebooks/score_tuning.ipynb` 做因子权重 grid search
