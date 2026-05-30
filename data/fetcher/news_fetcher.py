"""
news_fetcher.py — 持股期间新闻事件获取与分类

数据来源: AKShare stock_news_em（东方财富个股新闻）
LLM 分类: Claude Haiku（仅对高价值案例触发，控制 API 成本）
"""

import json
import logging
from datetime import datetime

import pandas as pd

from utils.retry import safe_akshare_call

logger = logging.getLogger(__name__)

# 不值得触发 LLM 分析的最低错失阈值（%）
_LLM_TRIGGER_MISSED_THRESHOLD = 5.0
# 不值得触发 LLM 分析的最低亏损阈值（%，负数）
_LLM_TRIGGER_LOSS_THRESHOLD = -3.0


def fetch_news_during_hold(
    code: str,
    buy_date: str,
    sell_date: str,
) -> pd.DataFrame:
    """
    拉取持股期间（buy_date ≤ 发布时间 ≤ sell_date）的东方财富个股新闻。

    Args:
        code:      股票代码（6位，如 "600519"）
        buy_date:  买入日期（YYYYMMDD）
        sell_date: 卖出日期（YYYYMMDD）

    Returns:
        DataFrame: title / publish_time / url
        失败或无数据时返回空 DataFrame
    """
    try:
        import akshare as ak

        raw = safe_akshare_call(
            ak.stock_news_em,
            symbol=code,
            max_retries=3,
            base_delay=2.0,
            fallback=None,
        )
        if raw is None or raw.empty:
            logger.info(f"[news_fetcher] [{code}] 无新闻数据")
            return pd.DataFrame()

        # 统一列名（AKShare 返回列名可能变动）
        col_map = {
            "新闻标题": "title",
            "发布时间": "publish_time",
            "新闻链接": "url",
            "标题":    "title",
            "时间":    "publish_time",
            "链接":    "url",
        }
        raw = raw.rename(columns={k: v for k, v in col_map.items() if k in raw.columns})

        required = {"title", "publish_time"}
        if not required.issubset(raw.columns):
            logger.warning(f"[news_fetcher] [{code}] 新闻列名异常: {raw.columns.tolist()}")
            return pd.DataFrame()

        # 解析时间，过滤持股日期范围
        raw["publish_time"] = pd.to_datetime(raw["publish_time"], errors="coerce")
        raw = raw.dropna(subset=["publish_time"])

        buy_dt  = pd.Timestamp(buy_date)
        sell_dt = pd.Timestamp(sell_date) + pd.Timedelta(days=1)  # 包含卖出当天
        mask    = (raw["publish_time"] >= buy_dt) & (raw["publish_time"] < sell_dt)
        result  = raw[mask].copy()

        if result.empty:
            logger.info(f"[news_fetcher] [{code}] {buy_date}~{sell_date} 无新闻")
            return pd.DataFrame()

        result["event_date"] = result["publish_time"].dt.strftime("%Y%m%d")
        result = result[["title", "publish_time", "event_date", "url"] if "url" in result.columns
                        else ["title", "publish_time", "event_date"]]

        logger.info(f"[news_fetcher] [{code}] 找到 {len(result)} 条新闻")
        return result.reset_index(drop=True)

    except Exception as e:
        logger.error(f"[news_fetcher] fetch_news_during_hold [{code}] 失败: {e}")
        return pd.DataFrame()


def classify_news_sentiment(
    news_df: pd.DataFrame,
    trade_id: int,
    code: str,
) -> list[dict]:
    """
    用 Claude Haiku 批量分类新闻情感 + 提取关键词，写入 sim_news_events。

    Args:
        news_df:  fetch_news_during_hold() 返回的 DataFrame
        trade_id: 关联 sim_trades.id
        code:     股票代码

    Returns:
        写入数据库的 event 记录列表
    """
    if news_df.empty:
        return []

    titles = news_df["title"].tolist()
    if not titles:
        return []

    try:
        import anthropic

        prompt = f"""请对以下A股新闻标题进行情感分类，并提取关键词。
股票代码: {code}

新闻列表（每行一条）：
{chr(10).join(f"{i+1}. {t}" for i, t in enumerate(titles))}

请以 JSON 数组格式返回，每个元素包含：
- index: 序号（从1开始）
- sentiment: "positive"（利好）/ "negative"（利空）/ "neutral"（中性）
- keywords: 关键词数组，如 ["机构增持", "业绩超预期"]，最多3个

只返回 JSON，不要其他文字。"""

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text.strip()

        # 解析 JSON
        parsed = _extract_json(raw_text)
        if not parsed:
            logger.warning(f"[news_fetcher] LLM 返回无法解析: {raw_text[:200]}")
            return _save_news_without_classification(news_df, trade_id, code)

        # 构建写入记录
        entries = []
        for item in parsed:
            idx = int(item.get("index", 0)) - 1
            if not (0 <= idx < len(news_df)):
                continue
            row = news_df.iloc[idx]
            entries.append({
                "trade_id":   trade_id,
                "code":       code,
                "event_date": str(row.get("event_date", "")),
                "title":      str(row.get("title", "")),
                "url":        str(row.get("url", "")) if "url" in row.index else "",
                "sentiment":  str(item.get("sentiment", "neutral")),
                "keywords":   json.dumps(item.get("keywords", []), ensure_ascii=False),
            })

        if entries:
            from skills.stock_sim_robot.sim_trade_db import save_news_events
            save_news_events(entries)
            logger.info(f"[news_fetcher] trade_id={trade_id} 写入 {len(entries)} 条新闻事件")

        return entries

    except Exception as e:
        logger.error(f"[news_fetcher] classify_news_sentiment trade_id={trade_id} 失败: {e}")
        return _save_news_without_classification(news_df, trade_id, code)


def _save_news_without_classification(
    news_df: pd.DataFrame,
    trade_id: int,
    code: str,
) -> list[dict]:
    """LLM 分类失败时，以 neutral 情感将新闻写入数据库"""
    entries = []
    for _, row in news_df.iterrows():
        entries.append({
            "trade_id":   trade_id,
            "code":       code,
            "event_date": str(row.get("event_date", "")),
            "title":      str(row.get("title", "")),
            "url":        str(row.get("url", "")) if "url" in row.index else "",
            "sentiment":  "neutral",
            "keywords":   "[]",
        })
    if entries:
        from skills.stock_sim_robot.sim_trade_db import save_news_events
        save_news_events(entries)
    return entries


def _extract_json(text: str) -> list | None:
    """从 LLM 响应中提取 JSON 数组"""
    text = text.strip()
    # 尝试找 [...] 块
    start = text.find("[")
    end   = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def fetch_and_classify_for_trade(trade_id: int) -> bool:
    """
    平仓时的入口函数：拉取新闻 + 判断是否触发 LLM 分类。

    触发 LLM 条件（满足任一）：
      - sell_type = '止损' 或 profit_pct < -3%
      - 同天有未买入候选股 T+5 收益 > 5%（错失机会）

    Args:
        trade_id: sim_trades.id

    Returns:
        是否成功处理
    """
    try:
        from sqlalchemy import text
        from skills.stock_sim_robot.sim_trade_db import _get_engine

        engine = _get_engine()
        sql = text(
            "SELECT code, trade_date, buy_price, sell_price, "
            "sell_date, sell_type, profit_pct "
            "FROM sim_trades WHERE id = :id LIMIT 1"
        )
        df = pd.read_sql(sql, engine, params={"id": int(trade_id)})
        if df.empty:
            return False

        row       = df.iloc[0]
        code      = str(row["code"])
        buy_date  = str(row["trade_date"])   # T 日龙虎榜日期 ≈ 买入日前一天
        sell_date = str(row.get("sell_date") or "")
        sell_type = str(row.get("sell_type") or "")
        profit_pct = float(row.get("profit_pct") or 0)

        if not sell_date:
            return False

        # 拉取新闻
        news_df = fetch_news_during_hold(code, buy_date, sell_date)
        if news_df.empty:
            return True  # 无新闻，正常返回

        # 判断是否需要 LLM 分类
        is_loss_trade = (sell_type == "止损") or (profit_pct < _LLM_TRIGGER_LOSS_THRESHOLD)

        is_missed_opportunity = False
        try:
            from modules.comparison_analyzer import analyze_same_day_candidates
            summary = analyze_same_day_candidates(buy_date)
            missed  = summary.get("missed_ret")
            if missed is not None and missed > _LLM_TRIGGER_MISSED_THRESHOLD:
                is_missed_opportunity = True
        except Exception:
            pass

        if is_loss_trade or is_missed_opportunity:
            classify_news_sentiment(news_df, trade_id, code)
        else:
            # 不触发 LLM，以 neutral 保存新闻记录
            _save_news_without_classification(news_df, trade_id, code)

        return True

    except Exception as e:
        logger.error(f"[news_fetcher] fetch_and_classify_for_trade({trade_id}) 失败: {e}")
        return False
