"""
comparison_analyzer.py — 选股对比分析核心模块

提供以下分析能力：
1. 同天候选对比：评分 vs 实际 T+N 收益
2. 错失机会统计：未买入但涨幅最高的股票
3. 持股时间轴：价格变动 + 新闻事件 + 技术信号
4. LLM 深度分析：高价值案例（错失>5% 或止损单）
"""

import json
import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ── 同天候选对比 ──────────────────────────────────────────────────────────────

def build_comparison_table(trade_date: str) -> pd.DataFrame:
    """
    生成同天候选对比表。

    列：代码 / 名称 / 评分 / 评分排名 / T+1收益 / T+5收益 / T+10收益 /
        收益排名(T+5) / 是否买入 / 评分vs收益差距 / 未选主因

    Returns:
        DataFrame，按评分排名升序排列
    """
    from config.settings import STORAGE_MODE
    if STORAGE_MODE != "postgres":
        logger.info("[comparison_analyzer] 非 PostgreSQL 模式，跳过")
        return pd.DataFrame()
    from skills.stock_sim_robot.sim_trade_db import get_candidate_results

    df = get_candidate_results(trade_date)
    if df.empty:
        return pd.DataFrame()

    df = df.copy()

    # 评分排名（评分高=排名1）—— fillna 避免 NaN 导致 astype(int) 失败
    df["评分排名"] = (
        df["signal_score"].rank(ascending=False, method="min")
        .fillna(0).astype(int)
    )

    # T+5 收益排名（收益高=排名1）
    if "t5_ret" in df.columns and df["t5_ret"].notna().any():
        df["收益排名T5"] = (
            df["t5_ret"].rank(ascending=False, method="min")
            .fillna(0).astype(int)
        )
    else:
        df["收益排名T5"] = None

    # 是否买入
    df["是否买入"] = df["trade_id"].notna()

    # 评分排名 vs 收益排名的差距（正数=评分高估了该股）
    if df["收益排名T5"].notna().any():
        df["排名差距"] = df["评分排名"] - df["收益排名T5"]
    else:
        df["排名差距"] = None

    # 未选主因（从 llm_rank_reason 摘取，候选股才有意义）
    df["未选主因"] = df.apply(
        lambda r: _extract_not_selected_reason(r) if not r["是否买入"] else "—",
        axis=1,
    )

    result = df[[
        "code", "name", "signal_score", "评分排名",
        "t1_ret", "t5_ret", "t10_ret", "收益排名T5",
        "是否买入", "排名差距", "未选主因",
        "lianban_count", "reason_tech", "reason_fund", "llm_rank_reason",
    ]].rename(columns={
        "code":         "代码",
        "name":         "名称",
        "signal_score": "评分",
        "t1_ret":       "T+1收益%",
        "t5_ret":       "T+5收益%",
        "t10_ret":      "T+10收益%",
        "lianban_count": "连板数",
    })

    return result.sort_values("评分排名").reset_index(drop=True)


def _extract_not_selected_reason(row: pd.Series) -> str:
    """从 llm_rank_reason 提取未选原因摘要（取前 60 字）"""
    text = str(row.get("llm_rank_reason") or "")
    if not text or text == "nan":
        # 降级：拼接技术/资金面原因摘要
        parts = []
        for field in ["reason_tech", "reason_fund"]:
            v = str(row.get(field) or "")
            if v and v != "nan":
                parts.append(v[:30])
        return "；".join(parts)[:60] if parts else "无记录"
    return text[:60]


def analyze_same_day_candidates(trade_date: str) -> dict:
    """
    同天候选汇总分析。

    Returns:
        dict 包含:
          - total: 候选总数
          - bought_code: 买入股代码
          - best_t5_code: T+5 收益最高的股代码
          - best_t5_ret: 最高 T+5 收益%
          - bought_t5_ret: 买入股 T+5 收益%
          - missed_ret: 错失收益（最高 - 买入股）
          - selected_optimal: 是否选中最优（True/False）
          - score_correlation: 评分与 T+5 收益的 Spearman 相关系数
          - comparison_table: build_comparison_table() 结果
    """
    from config.settings import STORAGE_MODE
    if STORAGE_MODE != "postgres":
        logger.info("[comparison_analyzer] 非 PostgreSQL 模式，跳过")
        return {"error": "非 PostgreSQL 模式"}
    table = build_comparison_table(trade_date)
    if table.empty:
        return {"error": "无候选数据"}

    bought = table[table["是否买入"]]
    not_bought = table[~table["是否买入"]]

    bought_code    = bought.iloc[0]["代码"] if not bought.empty else None
    bought_t5      = float(bought.iloc[0]["T+5收益%"]) if not bought.empty and pd.notna(bought.iloc[0]["T+5收益%"]) else None

    best_row       = table.loc[table["T+5收益%"].idxmax()] if table["T+5收益%"].notna().any() else None
    best_t5_code   = str(best_row["代码"]) if best_row is not None else None
    best_t5_ret    = float(best_row["T+5收益%"]) if best_row is not None else None

    missed_ret = None
    if best_t5_ret is not None and bought_t5 is not None:
        missed_ret = round(best_t5_ret - bought_t5, 2)

    selected_optimal = (bought_code == best_t5_code) if bought_code and best_t5_code else None

    # Spearman 相关系数（评分 vs T+5 收益）
    score_corr = None
    valid = table[table["T+5收益%"].notna() & table["评分"].notna()]
    if len(valid) >= 3:
        try:
            score_corr = round(
                valid["评分"].corr(valid["T+5收益%"], method="spearman"), 3
            )
        except Exception:
            pass

    return {
        "trade_date":        trade_date,
        "total":             len(table),
        "bought_code":       bought_code,
        "best_t5_code":      best_t5_code,
        "best_t5_ret":       best_t5_ret,
        "bought_t5_ret":     bought_t5,
        "missed_ret":        missed_ret,
        "selected_optimal":  selected_optimal,
        "score_correlation": score_corr,
        "comparison_table":  table,
    }


# ── 错失机会统计 ──────────────────────────────────────────────────────────────

def find_missed_opportunities(
    start_date: str,
    end_date: str,
    threshold: float = 5.0,
) -> pd.DataFrame:
    """
    在日期范围内找出所有错失机会（未买入但 T+5 收益 > threshold%）。

    Args:
        start_date: YYYYMMDD
        end_date:   YYYYMMDD
        threshold:  最低错失收益（默认 5.0%）

    Returns:
        DataFrame，含：日期/代码/名称/T+5收益/评分排名/买入股T+5收益/错失差距/未选主因
    """
    from config.settings import STORAGE_MODE
    if STORAGE_MODE != "postgres":
        logger.info("[comparison_analyzer] 非 PostgreSQL 模式，跳过")
        return pd.DataFrame()
    try:
        from sqlalchemy import text
        from skills.stock_sim_robot.sim_trade_db import _get_engine

        engine = _get_engine()
        sql = text("""
            SELECT
                sl.trade_date, sl.code, sl.name,
                sl.signal_score, sl.rank AS signal_rank,
                sl.llm_rank_reason,
                cr.t5_ret,
                -- 同天买入股的 T+5 收益
                bought.t5_ret AS bought_t5_ret,
                bought.code   AS bought_code
            FROM sim_selection_log sl
            JOIN sim_candidate_results cr ON cr.selection_id = sl.id
            LEFT JOIN (
                SELECT sl2.trade_date, sl2.code, cr2.t5_ret
                FROM sim_selection_log sl2
                JOIN sim_candidate_results cr2 ON cr2.selection_id = sl2.id
                WHERE sl2.trade_id IS NOT NULL
            ) bought ON bought.trade_date = sl.trade_date
            WHERE sl.trade_id IS NULL           -- 未买入的候选股
              AND cr.t5_ret >= :threshold
              AND sl.trade_date BETWEEN :start_date AND :end_date
            ORDER BY cr.t5_ret DESC
        """)
        df = pd.read_sql(sql, engine, params={
            "threshold":  threshold,
            "start_date": start_date,
            "end_date":   end_date,
        })

        if df.empty:
            return df

        df["错失差距%"] = (df["t5_ret"] - df["bought_t5_ret"].fillna(0)).round(2)
        df["未选主因"] = df["llm_rank_reason"].fillna("").apply(lambda x: x[:60])

        return df[[
            "trade_date", "code", "name", "signal_rank", "signal_score",
            "t5_ret", "bought_code", "bought_t5_ret", "错失差距%", "未选主因",
        ]].rename(columns={
            "trade_date":    "日期",
            "code":          "代码",
            "name":          "名称",
            "signal_rank":   "评分排名",
            "signal_score":  "评分",
            "t5_ret":        "T+5收益%",
            "bought_code":   "实际买入",
            "bought_t5_ret": "买入股T+5%",
        })

    except Exception as e:
        logger.error(f"find_missed_opportunities 失败: {e}")
        return pd.DataFrame()


# ── 持股时间轴 ────────────────────────────────────────────────────────────────

def get_holding_timeline(trade_id: int) -> dict:
    """
    构建持股时间轴：每天价格变动 + 新闻事件 + 技术信号变化。

    Returns:
        dict 包含:
          - trade_info:   sim_trades 基本信息
          - price_log:    sim_price_log 盘中记录（聚合到日）
          - news_events:  sim_news_events 新闻事件列表
          - post_tracking: sim_post_tracking 平仓后跟踪
          - kline:        持仓期间完整 K 线（含平仓后 T+15）
          - events_by_date: 按日期组织的事件字典（用于 K 线标注）
    """
    from config.settings import STORAGE_MODE
    if STORAGE_MODE != "postgres":
        logger.info("[comparison_analyzer] 非 PostgreSQL 模式，跳过")
        return {"error": "非 PostgreSQL 模式"}
    try:
        from skills.stock_sim_robot.sim_trade_db import (
            get_post_tracking,
            get_news_events,
        )
        from modules.market_data import get_kline_raw

        # 获取交易基本信息
        trade_info = _get_trade_info(trade_id)
        if trade_info is None:
            return {"error": f"trade_id={trade_id} 不存在"}

        code       = trade_info["code"]
        buy_date   = trade_info.get("trade_date") or trade_info.get("buy_date", "")
        sell_date  = trade_info.get("sell_date") or ""

        # 平仓后跟踪
        post_tracking = get_post_tracking(trade_id)

        # 新闻事件
        news_events = get_news_events(trade_id)

        # K 线：从买入日前5天到平仓后15天
        end_for_kline = sell_date if sell_date else buy_date
        kline = get_kline_raw(code, buy_date, days=30)
        if kline is not None and not kline.empty:
            kline["日期"] = kline["日期"].astype(str).str.replace("-", "")

        # 按日期组织事件（用于前端 K 线标注）
        events_by_date: dict[str, list[dict]] = {}

        if not news_events.empty:
            for _, row in news_events.iterrows():
                d = str(row.get("event_date", ""))
                if not d:
                    continue
                events_by_date.setdefault(d, []).append({
                    "type":      "news",
                    "sentiment": row.get("sentiment", "neutral"),
                    "title":     str(row.get("title", ""))[:50],
                    "keywords":  _parse_keywords(row.get("keywords")),
                })

        if not post_tracking.empty:
            for _, row in post_tracking.iterrows():
                d = str(row.get("track_date", ""))
                if not d:
                    continue
                pct_vs_sell = row.get("pct_vs_sell")
                events_by_date.setdefault(d, []).append({
                    "type":        "post_track",
                    "track_day":   row.get("track_day"),
                    "pct_vs_sell": pct_vs_sell,
                    "pct_vs_buy":  row.get("pct_vs_buy"),
                })

        return {
            "trade_info":     trade_info,
            "post_tracking":  post_tracking.to_dict("records") if not post_tracking.empty else [],
            "news_events":    news_events.to_dict("records") if not news_events.empty else [],
            "kline":          kline.to_dict("records") if kline is not None and not kline.empty else [],
            "events_by_date": events_by_date,
        }

    except Exception as e:
        logger.error(f"get_holding_timeline trade_id={trade_id} 失败: {e}")
        return {"error": str(e)}


def _get_trade_info(trade_id: int) -> Optional[dict]:
    """从 sim_trades 取一笔交易的基本信息"""
    try:
        from sqlalchemy import text
        from skills.stock_sim_robot.sim_trade_db import _get_engine

        engine = _get_engine()
        sql = text("SELECT * FROM sim_trades WHERE id = :id LIMIT 1")
        df = pd.read_sql(sql, engine, params={"id": int(trade_id)})
        if df.empty:
            return None
        return df.iloc[0].to_dict()
    except Exception as e:
        logger.error(f"_get_trade_info({trade_id}) 失败: {e}")
        return None


def _parse_keywords(keywords_raw) -> list[str]:
    if not keywords_raw or str(keywords_raw) == "nan":
        return []
    try:
        return json.loads(str(keywords_raw))
    except Exception:
        return [str(keywords_raw)]


# ── LLM 深度分析 ──────────────────────────────────────────────────────────────

def generate_llm_deep_analysis(trade_id: int) -> str:
    """
    对高价值案例生成 LLM 深度分析文字。
    条件：sell_type='止损' 或同天有候选股 T+5 收益 > 5%（错失机会）

    Returns:
        分析文字字符串，失败时返回空字符串
    """
    try:
        import anthropic

        timeline = get_holding_timeline(trade_id)
        if "error" in timeline:
            return ""

        trade_info = timeline["trade_info"]
        code       = trade_info.get("code", "")
        name       = trade_info.get("name", "")
        buy_price  = trade_info.get("buy_price")
        sell_price = trade_info.get("sell_price")
        profit_pct = trade_info.get("profit_pct")
        sell_type  = trade_info.get("sell_type", "")
        trade_date = trade_info.get("trade_date", "")

        # 同天对比数据
        comparison = analyze_same_day_candidates(trade_date)
        missed_ret = comparison.get("missed_ret")
        best_code  = comparison.get("best_t5_code")
        best_ret   = comparison.get("best_t5_ret")

        # 判断是否值得 LLM 深度分析
        is_loss      = (sell_type == "止损") or (profit_pct is not None and profit_pct < -3)
        is_missed    = (missed_ret is not None and missed_ret > 5)
        if not (is_loss or is_missed):
            return ""

        # 新闻事件摘要
        news_summary = ""
        if timeline["news_events"]:
            pos_news = [e["title"] for e in timeline["news_events"] if e.get("sentiment") == "positive"]
            neg_news = [e["title"] for e in timeline["news_events"] if e.get("sentiment") == "negative"]
            if pos_news:
                news_summary += f"利好新闻: {'; '.join(pos_news[:3])}\n"
            if neg_news:
                news_summary += f"利空新闻: {'; '.join(neg_news[:3])}\n"

        # 平仓后表现
        post_summary = ""
        if timeline["post_tracking"]:
            pts = {p["track_day"]: p for p in timeline["post_tracking"]}
            for day in [5, 10]:
                if day in pts:
                    post_summary += f"T+{day}: {pts[day].get('pct_vs_sell', 'N/A')}%（相对卖出价）\n"

        prompt = f"""你是一名专业的A股量化交易复盘分析师。请对以下一笔模拟交易进行深度复盘分析。

## 交易基本信息
- 股票: {name}({code})
- 龙虎榜日期: {trade_date}
- 买入价: {buy_price}  卖出价: {sell_price}
- 盈亏: {profit_pct}%  卖出类型: {sell_type}

## 同天候选对比
- 同天评分最高→收益最高的股票: {best_code}，T+5收益 {best_ret}%
- 我们选择的股票 T+5 收益: {comparison.get('bought_t5_ret')}%
- 错失收益差距: {missed_ret}%
- 评分与收益相关性: {comparison.get('score_correlation')}（-1到1，越高越好）

## 持股期间事件
{news_summary if news_summary else '无新闻记录'}

## 平仓后走势
{post_summary if post_summary else '暂无跟踪数据'}

请从以下3个维度给出分析（每点2-3句，总字数控制在300字内）：
1. **选股决策复盘**：为什么没选表现更好的股票？评分体系哪个维度可能权重偏低？
2. **持仓/卖出决策**：买入逻辑事后看是否成立？卖出时机是否合理？
3. **可执行改进建议**：下次遇到类似情况，有什么具体的操作规则可以改进？"""

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    except Exception as e:
        logger.error(f"generate_llm_deep_analysis trade_id={trade_id} 失败: {e}")
        return ""


# ── 周报摘要 ──────────────────────────────────────────────────────────────────

def build_weekly_review_summary(start_date: str, end_date: str) -> dict:
    """
    生成周报「本周选股复盘」区块所需数据。

    Returns:
        dict 包含:
          - missed_opportunities: 错失机会列表（T+5>5%）
          - sell_decision_stats:  卖出决策验证统计
          - top_missed:           最大错失机会（前3）
    """
    from config.settings import STORAGE_MODE
    if STORAGE_MODE != "postgres":
        logger.info("[comparison_analyzer] 非 PostgreSQL 模式，跳过")
        return {}
    missed = find_missed_opportunities(start_date, end_date, threshold=5.0)

    # 卖出决策验证：已平仓交易 T+5 表现
    sell_stats = _get_sell_decision_stats(start_date, end_date)

    top_missed = missed.head(3).to_dict("records") if not missed.empty else []

    return {
        "period":             f"{start_date}~{end_date}",
        "missed_count":       len(missed),
        "top_missed":         top_missed,
        "sell_decision_stats": sell_stats,
    }


def _get_sell_decision_stats(start_date: str, end_date: str) -> dict:
    """统计该时间段内卖出决策的事后验证结果（基于 sim_post_tracking）"""
    try:
        from sqlalchemy import text
        from skills.stock_sim_robot.sim_trade_db import _get_engine

        engine = _get_engine()
        sql = text("""
            SELECT
                t.id, t.code, t.name, t.sell_type, t.profit_pct,
                pt5.pct_vs_sell AS pct_t5_after_sell
            FROM sim_trades t
            LEFT JOIN sim_post_tracking pt5
                ON pt5.trade_id = t.id AND pt5.track_day = 5
            WHERE t.status = 'closed'
              AND t.sell_date BETWEEN :start_date AND :end_date
        """)
        df = pd.read_sql(sql, engine, params={
            "start_date": start_date,
            "end_date":   end_date,
        })

        if df.empty:
            return {}

        # 止损后继续跌（止损正确）
        stop_loss = df[df["sell_type"] == "止损"]
        stop_correct  = int((stop_loss["pct_t5_after_sell"] < 0).sum()) if not stop_loss.empty else 0
        stop_total    = len(stop_loss)

        # 止盈后继续涨（卖早了）
        take_profit = df[df["sell_type"].str.contains("止盈|分批", na=False)]
        sold_early  = int((take_profit["pct_t5_after_sell"] > 3).sum()) if not take_profit.empty else 0
        tp_total    = len(take_profit)

        return {
            "total_closed":      len(df),
            "stop_loss_total":   stop_total,
            "stop_loss_correct": stop_correct,
            "take_profit_total": tp_total,
            "sold_early_count":  sold_early,
        }
    except Exception as e:
        logger.error(f"_get_sell_decision_stats 失败: {e}")
        return {}
