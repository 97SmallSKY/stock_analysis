"""
ETL: Real tables → Logical tables (page-level storage)

Pages expect these logical table names (from Excel era) with Chinese column names.
Real data is in lhb_stock_daily, lhb_seats, kline_daily with English column names.

This script bridges the gap so pages can display data.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import psycopg2
import pandas as pd
from sqlalchemy import create_engine, text

PG_CONFIG = {
    "host": "117.72.184.8",
    "user": "lhb_admin",
    "password": "lhb_quant_2024",
    "dbname": "lhb_quant",
}

# SQLAlchemy URL for pandas to_sql
PG_URL = f"postgresql+psycopg2://{PG_CONFIG['user']}:{PG_CONFIG['password']}@{PG_CONFIG['host']}:5432/{PG_CONFIG['dbname']}"


def main():
    engine = create_engine(PG_URL)

    # ============================================================
    # 1. lhb_detail — 龙虎榜明细（AKShare 新浪格式）
    #    Pages expect: 代码, 名称, 收盘价, 偏离值, 成交量(万股),
    #                  成交额(万元), 上榜原因, trade_date
    # ============================================================
    print("=== 1/4: lhb_detail ===")
    df_detail = pd.read_sql("""
        SELECT
            ts_code AS "代码",
            name AS "名称",
            close_price AS "收盘价",
            pct_change AS "涨跌幅",
            ROUND(amount / 10000, 2) AS "成交额(万元)",
            l_buy AS "龙虎榜买入额",
            l_sell AS "龙虎榜卖出额",
            l_net AS "龙虎榜净买额",
            turnover_rate AS "换手率",
            reason AS "上榜原因",
            trade_date::text AS "trade_date"
        FROM lhb_stock_daily
        ORDER BY trade_date DESC, l_net DESC
    """, engine)
    print(f"  {len(df_detail)} rows")

    df_detail.to_sql("lhb_detail", engine, if_exists="replace", index=False, method="multi", chunksize=1000)
    print("  -> lhb_detail written")

    # ============================================================
    # 2. lhb_top10 — 每日 Top N（按净买额排名）
    #    Pages expect same columns as lhb_detail + rank
    # ============================================================
    print("=== 2/4: lhb_top10 ===")
    df_top10 = pd.read_sql("""
        SELECT * FROM (
            SELECT
                ts_code AS "代码",
                name AS "名称",
                close_price AS "收盘价",
                pct_change AS "涨跌幅",
                ROUND(amount / 10000, 2) AS "成交额(万元)",
                l_buy AS "龙虎榜买入额",
                l_sell AS "龙虎榜卖出额",
                l_net AS "龙虎榜净买额",
                turnover_rate AS "换手率",
                reason AS "上榜原因",
                trade_date::text AS "trade_date",
                ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY l_net DESC) AS "排名"
            FROM lhb_stock_daily
        ) sub WHERE "排名" <= 50
        ORDER BY trade_date DESC, "排名" ASC
    """, engine)
    print(f"  {len(df_top10)} rows")

    df_top10.to_sql("lhb_top10", engine, if_exists="replace", index=False, method="multi", chunksize=1000)
    print("  -> lhb_top10 written")

    # ============================================================
    # 3. lhb_jigou — 机构席位汇总
    #    From lhb_seats where is_institution = TRUE
    # ============================================================
    print("=== 3/4: lhb_jigou ===")
    df_jigou = pd.read_sql("""
        SELECT
            ls.ts_code AS "代码",
            MAX(lsd.name) AS "名称",
            ls.trade_date::text AS "trade_date",
            COUNT(*) FILTER (WHERE ls.direction = 'BUY') AS "买入次数",
            COUNT(*) FILTER (WHERE ls.direction = 'SELL') AS "卖出次数",
            COALESCE(SUM(ls.buy_amount), 0) AS "买入额(万元)",
            COALESCE(SUM(ls.sell_amount), 0) AS "卖出额(万元)",
            COALESCE(SUM(ls.net_amount), 0) AS "净买额(万元)"
        FROM lhb_seats ls
        JOIN lhb_stock_daily lsd ON lsd.id = ls.stock_daily_id
        WHERE ls.is_institution = TRUE
        GROUP BY ls.ts_code, ls.trade_date
        ORDER BY ls.trade_date DESC, "净买额(万元)" DESC
    """, engine)
    print(f"  {len(df_jigou)} rows")

    df_jigou.to_sql("lhb_jigou", engine, if_exists="replace", index=False, method="multi", chunksize=1000)
    print("  -> lhb_jigou written")

    # ============================================================
    # 4. lhb_ggtj — 个股上榜统计
    # ============================================================
    print("=== 4/4: lhb_ggtj ===")
    df_ggtj = pd.read_sql("""
        SELECT
            ts_code AS "代码",
            name AS "名称",
            COUNT(*) AS "上榜次数",
            COUNT(*) FILTER (WHERE l_net > 0) AS "净买入次数",
            ROUND(AVG(pct_change), 4) AS "平均涨跌幅",
            ROUND(SUM(l_net), 2) AS "累计净买额(万元)",
            ROUND(AVG(score), 2) AS "平均评分",
            MIN(trade_date)::text AS "首次上榜",
            MAX(trade_date)::text AS "最近上榜"
        FROM lhb_stock_daily
        GROUP BY ts_code, name
        ORDER BY "上榜次数" DESC
    """, engine)
    print(f"  {len(df_ggtj)} rows")

    df_ggtj.to_sql("lhb_ggtj", engine, if_exists="replace", index=False, method="multi", chunksize=1000)
    print("  -> lhb_ggtj written")

    # ============================================================
    # 5. stock_kline — K线数据映射
    # ============================================================
    print("=== 5/5: stock_kline ===")
    df_kline = pd.read_sql("""
        SELECT
            ts_code,
            trade_date::text AS "trade_date",
            open,
            high,
            low,
            close,
            pre_close,
            pct_change,
            vol,
            amount,
            turnover_rate
        FROM kline_daily
        ORDER BY ts_code, trade_date
    """, engine)

    # Write in chunks to avoid memory issues
    chunk_size = 50000
    for i in range(0, len(df_kline), chunk_size):
        chunk = df_kline.iloc[i:i+chunk_size]
        chunk.to_sql("stock_kline", engine, if_exists="replace" if i == 0 else "append",
                     index=False, method="multi", chunksize=1000)
        print(f"  stock_kline chunk {i//chunk_size + 1}: {len(chunk)} rows")
    print(f"  -> stock_kline: {len(df_kline)} total rows")

    # ============================================================
    # Verification
    # ============================================================
    print("\n=== Verification ===")
    with engine.connect() as conn:
        for tbl in ['lhb_detail', 'lhb_top10', 'lhb_jigou', 'lhb_ggtj', 'stock_kline']:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}"))
            cnt = result.fetchone()[0]
            print(f"  {tbl}: {cnt} rows {'✅' if cnt > 0 else '❌'}")

    print("\nETL complete!")


if __name__ == "__main__":
    main()
