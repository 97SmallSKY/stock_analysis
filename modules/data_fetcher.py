"""
数据获取模块
- 龙虎榜数据（AKShare）
- 个股K线数据
- 市场情绪数据
- 数据持久化到 Excel（后期切换到 PostgreSQL）

注意: AKShare 接口可能出现「当前系统繁忙，请稍后再试」的限流错误，
      已内置自动重试机制（最多 5 次，指数退避）。
"""
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from loguru import logger

import akshare as ak

from utils.storage import storage
from utils.retry import safe_akshare_call
from config.settings import (
    HALF_YEAR_START,
    DATA_END_DATE,
    FAMOUS_TRADERS,
    STRATEGY_CONFIG,
)


# ============================================================
# 龙虎榜数据  —  使用新浪财经接口（稳定，无严格频率限制）
# ============================================================

# 新浪龙虎榜明细列名 → 统一内部列名
_SINA_LHB_COL_MAP = {
    "股票代码": "代码",
    "股票名称": "名称",
    "收盘价":   "收盘价",
    "对应值":   "偏离值",       # 涨/跌幅偏离值
    "成交量":   "成交量(万股)",
    "成交额":   "成交额(万元)",
    "指标":     "上榜原因",
}


def fetch_lhb_detail(
    start_date: str = HALF_YEAR_START,
    end_date: str = DATA_END_DATE,
    save: bool = True,
) -> pd.DataFrame:
    """
    获取龙虎榜明细数据

    数据源：新浪财经  stock_lhb_detail_daily_sina
    - 每日一次请求，速度快（0.2s/日），不触发限流
    - 新浪接口只支持单日查询，需逐日循环，但间隔 0.3s 即可

    列名统一映射为内部标准列名（与东方财富兼容）：
        代码、名称、收盘价、偏离值、成交量(万股)、成交额(万元)、上榜原因、trade_date

    Args:
        start_date: 开始日期，格式 YYYYMMDD
        end_date: 结束日期，格式 YYYYMMDD
        save: 是否保存到本地

    Returns:
        DataFrame 龙虎榜明细
    """
    logger.info(f"开始获取龙虎榜数据（新浪）: {start_date} ~ {end_date}")

    all_data = []
    date_range = pd.date_range(start=start_date, end=end_date, freq="B")  # 仅交易日
    total = len(date_range)

    for i, date in enumerate(date_range):
        date_str = date.strftime("%Y%m%d")
        # 新浪接口：单次请求即可，重试 2 次足够（偶尔超时）
        df = safe_akshare_call(
            ak.stock_lhb_detail_daily_sina,
            date=date_str,
            max_retries=2,
            base_delay=1.5,
            fallback=None,
        )
        if df is not None and len(df) > 0:
            # 统一列名
            df = df.rename(columns={k: v for k, v in _SINA_LHB_COL_MAP.items() if k in df.columns})
            # 去掉「序号」列（无意义）
            df = df.drop(columns=["序号"], errors="ignore")
            df["trade_date"] = date_str
            all_data.append(df)
            logger.info(f"[{i+1}/{total}] {date_str}: {len(df)} 条")
        else:
            logger.debug(f"[{i+1}/{total}] {date_str}: 无数据（非交易日或接口暂无）")
        # 新浪接口无需太长间隔，0.3s 即可
        time.sleep(0.3)

    if not all_data:
        logger.warning("未获取到任何龙虎榜数据")
        return pd.DataFrame()

    result = pd.concat(all_data, ignore_index=True)
    logger.info(f"龙虎榜数据获取完成，共 {len(result)} 条")

    if save:
        storage.write(result, "lhb_detail", mode="append")

    return result


def fetch_lhb_jigou(
    days: int = 5,
    save: bool = True,
) -> pd.DataFrame:
    """
    获取近 N 天龙虎榜机构席位追踪数据

    数据源：新浪财经  stock_lhb_jgzz_sina
    - 一次请求返回近 N 天汇总，无需逐日循环
    - symbol 参数：'5' | '10' | '20' | '30' | '60'
    - 列名：股票代码、股票名称、累积买入额、买入次数、累积卖出额、卖出次数、净额

    Args:
        days: 统计天数（5/10/20/30/60）
        save: 是否保存

    Returns:
        DataFrame 机构席位汇总
    """
    symbol = str(days) if str(days) in ["5", "10", "20", "30", "60"] else "5"
    logger.info(f"获取机构席位追踪数据（近 {symbol} 天，新浪）")

    df = safe_akshare_call(
        ak.stock_lhb_jgzz_sina,
        symbol=symbol,
        max_retries=2,
        base_delay=1.5,
        fallback=None,
    )

    if df is not None and not df.empty:
        df["fetch_date"] = datetime.now().strftime("%Y%m%d")
        df["stat_days"]  = int(symbol)
        logger.info(f"机构席位数据获取完成，{len(df)} 条")
        if save:
            storage.write(df, "lhb_jigou", mode="replace")
        return df

    logger.error("获取机构席位数据失败")
    return pd.DataFrame()


def fetch_lhb_ggtj(
    days: int = 5,
    save: bool = True,
) -> pd.DataFrame:
    """
    获取近 N 天龙虎榜个股上榜统计（新浪）

    列名：股票代码、股票名称、上榜次数、累积购买额、累积卖出额、净额、买入席位数、卖出席位数

    注意：新浪接口实际可用的 symbol 参数为 5/10/30，
         20 和 60 返回空数据，会自动除外并选择最接近的小于等于值。

    Args:
        days: 统计天数（5/10/20/30/60，实际映射到最接近小于等于的可用值）

    Returns:
        DataFrame
    """
    # 新浪实际可用 symbol: 5, 10, 30
    _VALID = [5, 10, 30]
    # 选择最接近且小于等于 days 的其中一个
    valid_sym = max((v for v in _VALID if v <= days), default=5)
    symbol = str(valid_sym)

    df = safe_akshare_call(
        ak.stock_lhb_ggtj_sina,
        symbol=symbol,
        max_retries=2,
        base_delay=1.5,
        fallback=None,
    )
    if df is not None and not df.empty:
        df["fetch_date"] = datetime.now().strftime("%Y%m%d")
        df["stat_days"]  = valid_sym
        logger.info(f"个股上榜统计获取完成，{len(df)} 条（近{symbol}天）")
        if save:
            storage.write(df, "lhb_ggtj", mode="replace")
        return df
    logger.warning(f"个股上榜统计获取失败 symbol={symbol}")
    return pd.DataFrame()


def fetch_lhb_netbuy_by_date(trade_date: str, top_n: int = 50) -> pd.DataFrame:
    """
    获取指定交易日龙虎榜净买入 Top N 股票（东方财富接口）

    优先从数据库缓存读取（lhb_top10 表），无缓存才调用接口并存库。
    默认 top_n=50（覆盖当日大部分净买入正值股票），主板策略从中筛选。

    接口：ak.stock_lhb_detail_em(start_date, end_date)
    返回字段：代码、名称、上榜日、龙虎榜净买额、龙虎榜买入额、龙虎榜卖出额、
             涨跌幅、上榜后1日、上榜后2日、上榜后5日、上榜后10日、解读、上榜原因

    Args:
        trade_date: 格式 YYYYMMDD
        top_n: 取净买额前 N 名（默认50，覆盖更多主板候选）

    Returns:
        DataFrame，按净买额降序，去重（同一只股票可能因多条上榜原因出现多行，取净买额最大行）
    """
    logger.info(f"获取 {trade_date} 龙虎榜净买额 Top{top_n}（东方财富）")

    # ── 1. 优先读数据库缓存 ──────────────────────────────────────
    try:
        cached = storage.query_by_date("lhb_top10", trade_date)
        if not cached.empty:
            logger.info(f"✅ {trade_date} 龙虎榜 Top10 命中 DB 缓存，{len(cached)} 条")
            # 确保 trade_date 列存在
            if "trade_date" not in cached.columns:
                cached["trade_date"] = trade_date
            # 数值列恢复为 float（从 DB 读出可能是 object）
            for col in ["龙虎榜净买额", "龙虎榜买入额", "龙虎榜卖出额"]:
                if col in cached.columns:
                    cached[col] = pd.to_numeric(cached[col], errors="coerce")
            return cached.head(top_n).reset_index(drop=True)
    except Exception as e:
        logger.warning(f"读取 DB 缓存失败，降级走接口: {e}")

    # ── 2. 调用东方财富接口 ──────────────────────────────────────
    df = safe_akshare_call(
        ak.stock_lhb_detail_em,
        start_date=trade_date,
        end_date=trade_date,
        max_retries=4,
        base_delay=5.0,
        fallback=None,
    )

    if df is None or df.empty:
        # ── 3. 降级：尝试从本地 lhb_detail 生成近似排名 ──────────
        logger.warning(f"东方财富接口 {trade_date} 无数据，尝试本地 lhb_detail 降级")
        local = storage.read("lhb_detail")
        if not local.empty:
            day_local = local[local["trade_date"].astype(str) == trade_date]
            if not day_local.empty:
                amount_col = "成交额(万元)" if "成交额(万元)" in day_local.columns else None
                if amount_col:
                    top = day_local.nlargest(top_n, amount_col)[["代码", "名称", amount_col]].copy()
                    top = top.rename(columns={amount_col: "龙虎榜净买额"})
                    top["龙虎榜买入额"] = None
                    top["龙虎榜卖出额"] = None
                    for col in ["上榜后1日", "上榜后2日", "上榜后5日", "上榜后10日"]:
                        top[col] = None
                    top["排名"] = range(1, len(top) + 1)
                    top["trade_date"] = trade_date
                    top["_from_local"] = True
                    logger.info(f"降级成功：本地 lhb_detail 取 {trade_date} 共 {len(top)} 条")
                    return top.reset_index(drop=True)
        logger.warning(f"{trade_date} 无数据（接口+本地均失败）")
        return pd.DataFrame()

    # 标准化列名：兼容不同版本
    col_map = {}
    for c in df.columns:
        if c in ("股票代码", "代码"):
            col_map[c] = "代码"
        elif c in ("股票名称", "名称"):
            col_map[c] = "名称"
    df = df.rename(columns=col_map)

    net_col = "龙虎榜净买额"
    if net_col not in df.columns:
        logger.error(f"接口返回数据缺少 '{net_col}' 列，实际列：{list(df.columns)}")
        return pd.DataFrame()

    # 同一只股票可能多行（多条上榜原因），取净买额最大的那行
    df = df.sort_values(net_col, ascending=False)
    df = df.drop_duplicates(subset=["代码"], keep="first")

    # 按净买额降序取 Top N
    df = df.nlargest(top_n, net_col).reset_index(drop=True)
    df["排名"] = range(1, len(df) + 1)
    df["trade_date"] = trade_date

    # ── 4. 写入 DB 缓存（按日期 append，重复日期自动覆盖）────────
    try:
        storage.write(df, "lhb_top10", mode="append")
        logger.info(f"✅ {trade_date} 龙虎榜 Top10 已存入 DB 缓存")
    except Exception as e:
        logger.warning(f"存 DB 缓存失败（不影响返回）: {e}")

    logger.info(f"{trade_date} 净买额 Top{top_n} 获取完成，{len(df)} 条")
    return df


def fetch_lhb_jigou_today() -> pd.DataFrame:
    """
    实时拉取今日机构席位净买入数据（新浪接口）

    返回 DataFrame，列：股票代码、股票名称、交易日期、机构席位买入额、机构席位卖出额、净买额
    按净买额降序排列
    """
    logger.info("实时拉取今日机构席位净买入数据（新浪）")
    df = safe_akshare_call(
        ak.stock_lhb_jgmx_sina,
        max_retries=2,
        base_delay=1.5,
        fallback=None,
    )
    if df is None or df.empty:
        logger.warning("今日机构席位数据为空")
        return pd.DataFrame()

    # 标准化列名
    col_map = {}
    for c in df.columns:
        if "买入" in c:
            col_map[c] = "机构买入额"
        elif "卖出" in c:
            col_map[c] = "机构卖出额"
    df = df.rename(columns=col_map)

    if "机构买入额" in df.columns and "机构卖出额" in df.columns:
        df["净买额"] = df["机构买入额"] - df["机构卖出额"]
    else:
        df["净买额"] = 0

    df = df.sort_values("净买额", ascending=False).reset_index(drop=True)
    logger.info(f"今日机构数据：{len(df)} 条")
    return df


def get_lhb_top10_by_date(trade_date: str) -> pd.DataFrame:
    """
    获取指定交易日的龙虎榜 Top10 股票

    - 今天：实时拉取机构净买入数据，按净买额排名
    - 历史日期：从本地 lhb_detail 取成交额最高的 Top10

    Returns:
        DataFrame，列：股票代码、股票名称、排名依据（净买额 or 成交额）、排名值
    """
    today = datetime.now().strftime("%Y%m%d")

    if trade_date == today:
        # 今天：实时机构数据
        df = fetch_lhb_jigou_today()
        if not df.empty:
            code_col = "股票代码" if "股票代码" in df.columns else df.columns[0]
            name_col = "股票名称" if "股票名称" in df.columns else df.columns[1]
            result = df.head(10)[[code_col, name_col, "净买额"]].copy()
            result = result.rename(columns={code_col: "代码", name_col: "名称", "净买额": "排名值"})
            result["排名依据"] = "机构净买额(万元)"
            return result

    # 历史日期：用本地数据按成交额排名
    local = storage.read("lhb_detail")
    if local.empty:
        return pd.DataFrame()

    day_df = local[local["trade_date"].astype(str) == trade_date]
    if day_df.empty:
        return pd.DataFrame()

    amount_col = "成交额(万元)" if "成交额(万元)" in day_df.columns else None
    if amount_col:
        top = day_df.nlargest(10, amount_col)[["代码", "名称", amount_col]].copy()
        top = top.rename(columns={amount_col: "排名值"})
    else:
        top = day_df.head(10)[["代码", "名称"]].copy()
        top["排名值"] = 0

    top["排名依据"] = "成交额(万元)"
    return top.reset_index(drop=True)


def load_lhb_detail(
    start_date: str = None,
    end_date: str = None,
) -> pd.DataFrame:
    """
    从本地加载龙虎榜数据
    
    Args:
        start_date: 可选，按日期过滤
        end_date: 可选，按日期过滤
    """
    df = storage.read("lhb_detail")
    if df.empty:
        return df
    
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].astype(str)
        if start_date:
            df = df[df["trade_date"] >= start_date]
        if end_date:
            df = df[df["trade_date"] <= end_date]
    
    return df


# ============================================================
# 龙虎榜上榜后行情跟踪（T+1/T+3/T+5/T+10 盈亏）
# ============================================================

def calc_lhb_returns(
    lhb_df: pd.DataFrame = None,
    hold_days_list: list = None,
    max_stocks: int = 500,
    progress_callback=None,
) -> pd.DataFrame:
    """
    计算龙虎榜股票上榜后各持有期的收益率

    策略：T日上龙虎榜 → T+1开盘买入 → 分别在 T+hold_days 收盘卖出

    Args:
        lhb_df: 龙虎榜数据（None时自动从存储读取）
        hold_days_list: 持有天数列表，默认 [1, 3, 5, 10]
        max_stocks: 最多计算多少只股票（避免请求过多）
        progress_callback: 进度回调 fn(current, total, msg)

    Returns:
        DataFrame，每行一条上榜记录，附带各持有期的收益率列
        列：代码、名称、trade_date、上榜原因、收盘价、
            ret_1d、ret_3d、ret_5d、ret_10d（%，以T+1开盘为基准）
            buy_price（T+1开盘价）、成功获取（bool）
    """
    if hold_days_list is None:
        hold_days_list = [1, 3, 5, 10]

    if lhb_df is None or lhb_df.empty:
        lhb_df = storage.read("lhb_detail")

    if lhb_df.empty:
        logger.warning("没有龙虎榜数据，无法计算收益")
        return pd.DataFrame()

    # 去重：同一只股票同一天只算一次
    code_col = "代码" if "代码" in lhb_df.columns else "股票代码"
    lhb_dedup = lhb_df.drop_duplicates(subset=[code_col, "trade_date"]).copy()

    # 按日期排序，优先计算最近的
    lhb_dedup = lhb_dedup.sort_values("trade_date", ascending=False)
    if max_stocks:
        lhb_dedup = lhb_dedup.head(max_stocks)

    total = len(lhb_dedup)
    logger.info(f"开始计算 {total} 条龙虎榜记录的后续收益...")

    # 最大需要获取 T+hold_days+15 天的行情
    max_hold = max(hold_days_list)
    results = []

    for i, (_, row) in enumerate(lhb_dedup.iterrows()):
        code       = str(row[code_col]).zfill(6)
        name       = row.get("名称", row.get("股票名称", ""))
        trade_date = str(row["trade_date"])
        reason     = row.get("上榜原因", row.get("指标", ""))
        close_price = row.get("收盘价", 0)

        if progress_callback and (i % 10 == 0 or i == total - 1):
            progress_callback(i + 1, total, f"[{i+1}/{total}] {code} {name}")

        # 获取上榜日后续行情
        try:
            start_dt = trade_date
            end_dt = (pd.to_datetime(trade_date) + timedelta(days=max_hold + 20)).strftime("%Y%m%d")
            hist = safe_akshare_call(
                ak.stock_zh_a_hist,
                symbol=code,
                period="daily",
                start_date=start_dt,
                end_date=end_dt,
                adjust="qfq",
                max_retries=2,
                base_delay=1.5,
                fallback=None,
            )
        except Exception:
            hist = None

        rec = {
            "代码":       code,
            "名称":       name,
            "trade_date": trade_date,
            "上榜原因":   reason,
            "上榜收盘价": close_price,
            "buy_price":  None,
            "data_ok":    False,
        }
        for d in hold_days_list:
            rec[f"ret_{d}d"] = None

        if hist is not None and len(hist) >= 2:
            # 日期列名兼容
            date_col  = "日期" if "日期" in hist.columns else hist.columns[0]
            open_col  = "开盘" if "开盘" in hist.columns else None
            close_col = "收盘" if "收盘" in hist.columns else None

            if open_col and close_col:
                hist = hist.sort_values(date_col).reset_index(drop=True)
                # T 日在第 0 行，T+1 在第 1 行
                buy_price = float(hist.iloc[1][open_col])
                rec["buy_price"] = round(buy_price, 3)
                rec["data_ok"]   = True

                for d in hold_days_list:
                    idx = min(1 + d, len(hist) - 1)
                    sell_price = float(hist.iloc[idx][close_col])
                    if buy_price > 0:
                        ret = (sell_price / buy_price - 1) * 100
                        rec[f"ret_{d}d"] = round(ret, 2)

        results.append(rec)
        time.sleep(0.2)  # 不加频率限制会被封

    result_df = pd.DataFrame(results)
    logger.info(f"收益计算完成：{len(result_df)} 条，成功获取行情 {result_df['data_ok'].sum()} 条")

    # 保存到存储
    if not result_df.empty:
        storage.write(result_df, "lhb_returns", mode="replace")

    return result_df


def load_lhb_returns() -> pd.DataFrame:
    """从本地存储读取龙虎榜收益跟踪数据"""
    return storage.read("lhb_returns")


# ============================================================
# 个股 K 线数据
# ============================================================

def fetch_stock_kline(
    stock_code: str,
    start_date: str = HALF_YEAR_START,
    end_date: str = DATA_END_DATE,
    adjust: str = "qfq",
    save: bool = True,
) -> pd.DataFrame:
    """
    获取个股日K线数据
    
    Args:
        stock_code: 股票代码（如 '600000'）
        start_date: 开始日期
        end_date: 结束日期
        adjust: 复权方式 qfq=前复权
    
    Returns:
        DataFrame K线数据
    """
    try:
        df = safe_akshare_call(
            ak.stock_zh_a_hist,
            symbol=stock_code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            max_retries=4,
            base_delay=2.0,
            fallback=None,
        )
        if df is not None and not df.empty:
            df["code"] = stock_code
            logger.debug(f"获取 {stock_code} K线数据 {len(df)} 条")
            
            if save:
                # 读取现有数据，追加新数据
                existing = storage.read("stock_kline")
                if not existing.empty and "code" in existing.columns:
                    existing = existing[existing["code"] != stock_code]
                    df = pd.concat([existing, df], ignore_index=True)
                storage.write(df, "stock_kline", mode="replace")
            
            return df
    except Exception as e:
        logger.error(f"获取 {stock_code} K线失败: {e}")
    return pd.DataFrame()


def get_next_day_return(
    stock_code: str,
    trade_date: str,
    hold_days: int = 1,
) -> dict:
    """
    计算 T+1 日的收益率
    
    Args:
        stock_code: 股票代码
        trade_date: 龙虎榜上榜日期（T日）
        hold_days: 持有天数
    
    Returns:
        包含买卖价格和收益率的字典
    """
    try:
        end_dt = pd.to_datetime(trade_date) + timedelta(days=hold_days + 15)
        df = safe_akshare_call(
            ak.stock_zh_a_hist,
            symbol=stock_code,
            period="daily",
            start_date=trade_date,
            end_date=end_dt.strftime("%Y%m%d"),
            adjust="qfq",
            max_retries=4,
            base_delay=2.0,
            fallback=None,
        )
        
        if df is None or len(df) < 2:
            return None
        
        buy_price = df.iloc[1]["开盘"]  # T+1 开盘买入
        
        if hold_days == 1:
            sell_price = df.iloc[1]["收盘"]
        else:
            idx = min(1 + hold_days, len(df) - 1)
            sell_price = df.iloc[idx]["收盘"]
        
        return_rate = (sell_price - buy_price) / buy_price
        
        return {
            "buy_price": buy_price,
            "sell_price": sell_price,
            "return_rate": return_rate,
            "next_open": df.iloc[1]["开盘"],
            "next_high": df.iloc[1]["最高"],
            "next_low": df.iloc[1]["最低"],
            "next_close": df.iloc[1]["收盘"],
            "next_volume": df.iloc[1]["成交量"],
        }
    except Exception as e:
        logger.debug(f"获取 {stock_code} 次日收益失败: {e}")
        return None


# ============================================================
# 市场情绪数据
# ============================================================

def _load_sentiment_cache(trade_date: str) -> dict:
    """从本地存储读取指定日期的情绪缓存（降级用）"""
    try:
        df = storage.read("market_sentiment")
        if df.empty or "trade_date" not in df.columns:
            return {}
        row = df[df["trade_date"].astype(str) == trade_date]
        if not row.empty:
            return row.iloc[-1].to_dict()
        # 没有当天的，取最近一条
        df = df.sort_values("trade_date", ascending=False)
        if not df.empty:
            return df.iloc[0].to_dict()
    except Exception:
        pass
    return {}


def fetch_market_sentiment(trade_date: str = None, save: bool = True) -> dict:
    """
    获取市场情绪数据（涨跌停家数、连板、大盘趋势）

    容错策略：
    - 若接口返回「当前系统繁忙」，safe_akshare_call 自动重试 5 次
    - 重试全部失败后，优先返回本地缓存数据（降级），不抛异常，不让页面崩溃

    Args:
        trade_date: 日期（默认今日）
        save: 是否保存

    Returns:
        情绪指标字典（含 _from_cache=True 标记表示来自缓存）
    """
    if not trade_date:
        trade_date = datetime.now().strftime("%Y%m%d")

    # 预先加载本地缓存，接口繁忙时降级
    _cached = _load_sentiment_cache(trade_date)

    sentiment = {"trade_date": trade_date, "_from_cache": False}

    # ── 1. 涨跌停家数（涨跌停池，仅支持最近30天）──────────────────
    # 超出范围或接口失败时直接走本地缓存，不用 stock_changes_em（参数不兼容）
    df_up = safe_akshare_call(
        ak.stock_zt_pool_em, date=trade_date,
        max_retries=2, base_delay=1.0, fallback=None,
    )

    df_down = safe_akshare_call(
        ak.stock_zt_pool_dtgc_em, date=trade_date,
        max_retries=2, base_delay=1.0, fallback=None,
    )

    if df_up is None and _cached:
        sentiment["limit_up"]      = int(_cached.get("limit_up", 0))
        sentiment["limit_down"]    = int(_cached.get("limit_down", 0))
        sentiment["_from_cache"]   = True
        logger.warning(f"涨跌停接口均失败，使用 {_cached.get('trade_date','?')} 日缓存")
    else:
        sentiment["limit_up"]   = len(df_up)   if df_up   is not None else 0
        sentiment["limit_down"] = len(df_down) if df_down is not None else 0

    sentiment["up_down_ratio"] = (
        sentiment["limit_up"] / max(sentiment["limit_down"], 1)
    )

    # ── 2. 连板股数量 ────────────────────────────────────────
    df_lian = safe_akshare_call(
        ak.stock_zt_pool_strong_em, date=trade_date,
        max_retries=2, base_delay=1.0, fallback=None,
    )
    if df_lian is None and _cached:
        sentiment["continuous_limit"] = int(_cached.get("continuous_limit", 0))
    else:
        sentiment["continuous_limit"] = len(df_lian) if df_lian is not None else 0

    # ── 3. 大盘趋势（上证指数日线）────────────────────────────
    index_df = safe_akshare_call(
        ak.stock_zh_index_daily, symbol="sh000001",
        max_retries=4, base_delay=2.0, fallback=None,
    )
    if index_df is not None and not index_df.empty:
        index_df = index_df.tail(30)
        ma5  = index_df["close"].tail(5).mean()
        ma20 = index_df["close"].tail(20).mean()
        last_close = index_df["close"].iloc[-1]
        sentiment["index_close"]  = round(float(last_close), 2)
        sentiment["index_ma5"]    = round(float(ma5), 2)
        sentiment["index_ma20"]   = round(float(ma20), 2)
        sentiment["index_trend"]  = "bullish" if ma5 > ma20 else "bearish"
        sentiment["index_change"] = round(
            (last_close / index_df["close"].iloc[-2] - 1) * 100, 2
        )
    elif _cached:
        sentiment["index_close"]  = _cached.get("index_close", 0)
        sentiment["index_ma5"]    = _cached.get("index_ma5", 0)
        sentiment["index_ma20"]   = _cached.get("index_ma20", 0)
        sentiment["index_trend"]  = _cached.get("index_trend", "neutral")
        sentiment["index_change"] = _cached.get("index_change", 0)
        sentiment["_from_cache"]  = True
        logger.warning("大盘指数接口繁忙，使用本地缓存数据")
    else:
        sentiment["index_trend"]  = "neutral"
        sentiment["index_close"]  = 0
        sentiment["index_change"] = 0

    # ── 4. 交易信号判断 ──────────────────────────────────────
    cfg        = STRATEGY_CONFIG
    limit_up   = sentiment.get("limit_up", 0)
    limit_down = sentiment.get("limit_down", 0)
    trend      = sentiment.get("index_trend", "neutral")

    if limit_up < cfg["min_limit_up"] and limit_down > cfg["max_limit_down"]:
        sentiment["trade_signal"]  = "no_trade"
        sentiment["signal_reason"] = f"市场情绪差：涨停{limit_up}家，跌停{limit_down}家"
    elif trend == "bearish":
        sentiment["trade_signal"]  = "half_position"
        sentiment["signal_reason"] = "大盘趋势向下，建议半仓操作"
    elif limit_up > 50:
        sentiment["trade_signal"]  = "full_position"
        sentiment["signal_reason"] = f"赚钱效应强：涨停{limit_up}家"
    else:
        sentiment["trade_signal"]  = "normal"
        sentiment["signal_reason"] = "市场正常，正常操作"

    # 若来自缓存，在 signal_reason 中加标注
    if sentiment.get("_from_cache"):
        sentiment["signal_reason"] += f"（数据来自缓存，接口当前繁忙）"

    # ── 5. 保存 ──────────────────────────────────────────────
    if save and not sentiment.get("_from_cache"):
        df = pd.DataFrame([{k: v for k, v in sentiment.items() if k != "_from_cache"}])
        storage.write(df, "market_sentiment", mode="append")

    logger.info(
        f"市场情绪 [{trade_date}]: 涨停={sentiment.get('limit_up')}, "
        f"跌停={sentiment.get('limit_down')}, 大盘={sentiment.get('index_trend')}, "
        f"信号={sentiment.get('trade_signal')}, 缓存={sentiment.get('_from_cache')}"
    )
    return sentiment


def load_market_sentiment_history() -> pd.DataFrame:
    """加载历史市场情绪数据"""
    return storage.read("market_sentiment")


# ============================================================
# 数据初始化（拉取最近半年数据）
# ============================================================

def initialize_data(
    start_date: str = HALF_YEAR_START,
    end_date: str = DATA_END_DATE,
    force_refresh: bool = False,
) -> dict:
    """
    初始化/刷新最近半年的数据
    
    Args:
        start_date: 开始日期
        end_date: 结束日期
        force_refresh: 是否强制重新拉取
    
    Returns:
        包含各数据集的字典
    """
    logger.info(f"开始初始化数据: {start_date} ~ {end_date}")
    result = {}
    
    # 检查是否已有数据
    if not force_refresh:
        existing_lhb = storage.read("lhb_detail")
        if not existing_lhb.empty:
            logger.info(f"本地已有龙虎榜数据 {len(existing_lhb)} 条，跳过拉取（force_refresh=False）")
            result["lhb_detail"] = existing_lhb
            result["status"] = "cached"
            return result
    
    # 拉取龙虎榜
    lhb_df = fetch_lhb_detail(start_date, end_date, save=True)
    result["lhb_detail"] = lhb_df
    
    # 拉取机构席位汇总（新浪，一次拉全）
    jigou_df = fetch_lhb_jigou(days=20, save=True)
    result["lhb_jigou"] = jigou_df
    
    # 拉取个股上榜统计（新浪）
    ggtj_df = fetch_lhb_ggtj(days=20, save=True)
    result["lhb_ggtj"] = ggtj_df
    
    # 拉取市场情绪（最近20个工作日）
    market_dates = pd.date_range(
        start=(datetime.now() - timedelta(days=30)).strftime("%Y%m%d"),
        end=end_date,
        freq="B",
    )
    sentiment_list = []
    for date in market_dates[-20:]:
        s = fetch_market_sentiment(date.strftime("%Y%m%d"), save=False)
        sentiment_list.append(s)
        time.sleep(0.3)
    
    if sentiment_list:
        sentiment_df = pd.DataFrame(sentiment_list)
        storage.write(sentiment_df, "market_sentiment", mode="replace")
        result["market_sentiment"] = sentiment_df
    
    result["status"] = "refreshed"
    logger.info("数据初始化完成")
    return result
