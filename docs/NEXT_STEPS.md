# 下一阶段开发方案 (Data Ingestion / W2 里程碑)

> 更新日期: 2026-04-17
> 对应 roadmap: W2 阶段 —— 把历史数据灌入 PostgreSQL / TimescaleDB

## 一、为什么这是下一步

现状：
- 数据库 schema (`db/init/01_schema.sql`) 已就位
- 多数据源网关 (`backend/data_gateway/*`) 已完成并通过 47 条单测
- 但是 **库里还没有一行数据** —— 下游所有模块（龙虎榜分析、回测、策略、前端看板）都依赖历史数据，否则无法开展

所以 W2 的关键任务是：**把数据从数据源灌入数据库**。在此之前，既不能做回测也不能做策略验证。

## 二、本阶段交付物

| # | 交付 | 文件 | 说明 |
|---|------|------|------|
| 1 | CLI 入口 | `backend/cli.py` | `python -m backend.cli ingest ...` |
| 2 | Makefile | `Makefile` | 本地/远程一行命令抓数 |
| 3 | 配置加载 | `backend/config.py` | 读取 `.env` → dataclass settings |
| 4 | DB 会话 | `backend/db/session.py` | psycopg2 连接上下文 `pg_conn`(不使用 ORM,见 ADR-0001) |
| 5 | 写入助手 | `backend/db/writer.py` | upsert 封装（ON CONFLICT） |
| 6 | ingest 基类 | `backend/ingest/base.py` | 进度条/批量/错误捕获 |
| 7 | 股票信息 | `backend/ingest/stock_info.py` | `stock_info` 全量刷新 |
| 8 | 交易日历 | `backend/ingest/calendar.py` | `trade_calendar` |
| 9 | 日K线 | `backend/ingest/kline.py` | `kline_daily`（增量） |
| 10 | 龙虎榜 | `backend/ingest/lhb.py` | `lhb_stock_daily` |
| 11 | 情绪 | `backend/ingest/sentiment.py` | `market_sentiment`（W3 补） |

## 三、数据优先级

TuShare 120 积分**只能拿日 K**，因此默认链：

| 数据类别 | 主源 | 备源 |
|----------|------|------|
| STOCK_INFO | AKShare | Sina |
| TRADE_CALENDAR | AKShare | — |
| DAILY_KLINE | TuShare(120点) | AKShare |
| LHB_DETAIL | AKShare | Crawler |
| MARKET_SENTIMENT | AKShare | — |

所有源通过 `DataSourceManager` 统一访问，失败自动降级。

## 四、Makefile 使用

```bash
# 环境
make install          # pip install -r requirements.txt
make test             # pytest

# 数据抓取（本地开发指向 jd DB 即可）
make ingest-stock-info                                # 股票清单
make ingest-calendar                                  # 交易日历
make ingest-kline FROM=20200101 TO=20260417          # 全市场日K
make ingest-kline CODES=000001.SZ FROM=20200101      # 单只
make ingest-lhb FROM=20240101 TO=20260417            # 龙虎榜
make ingest-sentiment FROM=20240101 TO=20260417      # 情绪
make ingest-all                                       # 一键全量

# 服务
make deploy           # ssh jd 拉代码 + 重启
make restart          # 重启 api/worker/scheduler
make healthcheck      # 健康检查
```

## 五、执行次序（首次灌库）

```
1. make ingest-stock-info          (~5000 行)
2. make ingest-calendar            (~2000 行)
3. make ingest-kline FROM=20200101 (~5年 × 5000只 ≈ 600w 行)
4. make ingest-lhb   FROM=20200101 (~200w 行)
5. make ingest-sentiment FROM=...
```

耗时估计：日K 全量 ≈ 2~4 小时（AKShare），建议在 jd 服务器 `tmux` 内跑。

## 六、W3 规划预览

数据进库后立刻进入 W3：**龙虎榜评分 + FastAPI 只读接口 + 最小前端列表**。
一旦评分能跑，策略/回测并行推进。
