"""
📡 实时监控仪表盘（文档第四章）
================================
功能：
  1. 持仓卡片：MS动能评分 / V/P/B三维度 / 移动止盈线 / 预计卖出价 / 操作建议
  2. 状态指示灯（🟢正常 / 🟡延迟 / 🔴断流）
  3. 市场环境（上证/涨停/跌停数量）
  4. 自动刷新（非交易时段30秒, 盘中黄金15分钟30秒, 普通时段60秒）

数据源三级兜底：AKShare → 新浪 → 缓存

持仓来源：从 sim_trades（status=open）读取，或手动输入测试持仓
"""
from __future__ import annotations

import time
import json
from datetime import datetime, date
from typing import Optional

import pandas as pd
import streamlit as st

from modules.ms_scorer import (
    calculate_ms, MarketSnapshot, MSResult, MSMode, SceneType,
    ms_bar, ms_level_label, _get_price_limit, _is_trading_time,
)
from modules.realtime_fetcher import (
    fetch_snapshots_batch, fetch_snapshot,
    get_data_freshness_label,
)

# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

def _is_market_open() -> bool:
    """是否在交易时段"""
    return _is_trading_time(datetime.now().time())


def _refresh_interval() -> int:
    """
    根据当前时段返回推荐刷新间隔（秒）
    集合竞价 9:15~9:30: 10s
    黄金时段 9:30~10:00 / 14:30~15:00: 30s
    普通时段: 60s
    非交易时段: 300s
    """
    now = datetime.now().time()
    import datetime as dt_m
    t = now

    if dt_m.time(9, 15) <= t <= dt_m.time(9, 30):
        return 10
    elif dt_m.time(9, 30) <= t <= dt_m.time(10, 0):
        return 30
    elif dt_m.time(14, 30) <= t <= dt_m.time(15, 0):
        return 30
    elif _is_trading_time(t):
        return 60
    else:
        return 300


def _load_open_positions() -> list[dict]:
    """
    从 sim_trades 中读取持仓中（status=open）的记录
    返回格式：[{"code": "600519", "name": "贵州茅台", "buy_price": 2001.0, "id": 1}, ...]
    """
    try:
        from utils.storage import storage
        engine = storage._get_engine()
        from sqlalchemy import text
        sql = text("""
            SELECT id, code, name, buy_price, trade_date
            FROM sim_trades
            WHERE status = 'open'
            ORDER BY created_at DESC
        """)
        df = pd.read_sql(sql, engine)
        if df.empty:
            return []
        records = []
        for _, row in df.iterrows():
            records.append({
                "id":         int(row["id"]),
                "code":       str(row["code"]).zfill(6),
                "name":       str(row["name"]),
                "buy_price":  float(row["buy_price"]) if pd.notna(row["buy_price"]) else 0.0,
                "trade_date": str(row["trade_date"]),
            })
        return records
    except Exception as e:
        return []


def _load_market_env() -> dict:
    """获取市场整体环境（上证涨幅 / 涨停数 / 跌停数）"""
    try:
        import akshare as ak
        # 上证指数
        df = ak.stock_zh_index_spot_sina(symbol="上证综指")
        if df is not None and not df.empty:
            close = float(df["最新价"].iloc[0])
            pct   = float(df["涨跌幅"].iloc[0])
        else:
            close, pct = 0.0, 0.0
    except Exception:
        close, pct = 0.0, 0.0

    # 涨停/跌停数（来自每日数据，非实时，仅参考）
    limit_up = limit_down = 0
    try:
        import akshare as ak
        spot = ak.stock_zh_a_spot_em()
        if spot is not None and not spot.empty and "涨跌幅" in spot.columns:
            spot["_chg"] = pd.to_numeric(spot["涨跌幅"], errors="coerce")
            # 主板 ±10%, 创业板 ±20%，简单用 ≥9.9% 估计涨停
            limit_up   = int((spot["_chg"] >= 9.9).sum())
            limit_down = int((spot["_chg"] <= -9.9).sum())
    except Exception:
        pass

    return {
        "sh_close": close,
        "sh_pct":   pct,
        "limit_up": limit_up,
        "limit_down": limit_down,
    }


# ══════════════════════════════════════════════════════════════
# 渲染单张持仓卡片
# ══════════════════════════════════════════════════════════════

def _render_position_card(
    pos: dict,
    snap: MarketSnapshot,
    ms_result: MSResult,
    highest_since_buy: float,
):
    """渲染单只持仓的完整监控卡片"""
    code  = pos["code"]
    name  = pos.get("name", code)
    bp    = snap.buy_price
    cur   = snap.current
    pnl_pct = (cur - bp) / bp * 100 if bp > 0 else 0.0
    limit_lbl = f"±{snap.limit_up_pct:.0f}%"

    # 数据状态灯
    freshness_label, freshness_color = get_data_freshness_label(snap)

    # 场景→颜色映射
    scene_color = {
        SceneType.HOLD:           "#1e3a1e",  # 深绿底
        SceneType.REDUCE:         "#3a2d00",  # 深橙底
        SceneType.EXIT_WEAK:      "#3a0000",  # 深红底
        SceneType.LIMIT_UP_SEALED:"#1a1a3e",  # 深蓝底
        SceneType.HIGH_OPEN_FAIL: "#2a1a00",  # 深黄底
        SceneType.NEAR_STOPLOSS:  "#3a0000",  # 深红底
        SceneType.DATA_STALE:     "#2a2a00",  # 黄
        SceneType.CLOSED:         "#1a1a1a",  # 灰
    }.get(ms_result.scene, "#1a1a1a")

    border_color = {
        SceneType.HOLD:           "#2ECC71",
        SceneType.REDUCE:         "#F39C12",
        SceneType.EXIT_WEAK:      "#E74C3C",
        SceneType.LIMIT_UP_SEALED:"#3498DB",
        SceneType.HIGH_OPEN_FAIL: "#E67E22",
        SceneType.NEAR_STOPLOSS:  "#E74C3C",
        SceneType.DATA_STALE:     "#F1C40F",
        SceneType.CLOSED:         "#7F8C8D",
    }.get(ms_result.scene, "#7F8C8D")

    # MS 进度条颜色
    ms_bar_color = (
        "#2ECC71" if ms_result.ms >= 60
        else "#F39C12" if ms_result.ms >= 40
        else "#E74C3C"
    )
    bar_str = ms_bar(ms_result.ms)

    # 降级模式提示
    mode_hint = {
        MSMode.DEGRADED_NO_L2:  "（⚠️ 无L2封单数据，盘口维度降级）",
        MSMode.DEGRADED_NO_VOL: "（⚠️ 量比数据不可用，量能维度降级）",
        MSMode.PRICE_ONLY:      "（🔴 仅价格数据，止损阈值上移10分）",
        MSMode.UNAVAILABLE:     "（🔴 数据完全不可用）",
    }.get(ms_result.mode, "")

    # 预计卖出价展示
    est_sell_str = ""
    if ms_result.est_sell_price > 0:
        est_pnl = (ms_result.est_sell_price - bp) / bp * 100 if bp > 0 else 0.0
        est_sell_str = f"预计卖出价 <b>{ms_result.est_sell_price:.3f}</b>（盈亏估算 <b>{est_pnl:+.1f}%</b>）"
    elif ms_result.scene == SceneType.LIMIT_UP_SEALED:
        est_sell_str = "涨停封板中，暂不卖出"

    # 数据来源
    source_str = {
        "akshare_spot_em": "🟢 AKShare实时",
        "sina_hq":         "🟡 新浪备用",
        "none":            "🔴 断流",
    }.get(snap.data_source, snap.data_source)

    # 涨跌幅颜色
    pnl_html_color = "#2ECC71" if pnl_pct >= 0 else "#E74C3C"
    chg_html_color = "#2ECC71" if snap.chg_pct >= 0 else "#E74C3C"

    # 卡片 HTML
    board_label = {
        True: "沪/深主板" if not str(code).startswith("688") else "科创板",
    }.get(True, "")
    if str(code).startswith("688"):  board_label = "科创板"
    elif str(code).startswith("3"):  board_label = "创业板"
    elif str(code)[0] in ("8","4"):  board_label = "北交所"
    elif str(code).startswith("6") or str(code)[:3] in ("000","001","002","003"):
        board_label = "主板"
    else:
        board_label = ""

    trade_date_str = pos.get("trade_date", "")

    html = f"""
<div style="
  padding: 16px;
  background: {scene_color};
  border-left: 6px solid {border_color};
  border-radius: 8px;
  margin-bottom: 16px;
  font-family: monospace;
">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
    <div style="font-size:1.2em; font-weight:bold; color:#ECECE0;">
      {name}（{code}）<span style="color:#aaa; font-size:0.8em;">{board_label} 限幅{limit_lbl}</span>
    </div>
    <div style="font-size:0.85em; color:#aaa;">{freshness_label} | {source_str}</div>
  </div>

  <div style="display:grid; grid-template-columns:1fr 1fr 1fr 1fr; gap:8px; margin-bottom:12px;">
    <div>
      <div style="color:#999; font-size:0.75em;">买入价</div>
      <div style="font-size:1.1em;">¥{bp:.3f}</div>
      <div style="color:#888; font-size:0.7em;">上榜日 {trade_date_str}</div>
    </div>
    <div>
      <div style="color:#999; font-size:0.75em;">当前价</div>
      <div style="font-size:1.2em; font-weight:bold; color:{chg_html_color};">
        ¥{cur:.3f}
        <span style="font-size:0.85em;">({snap.chg_pct:+.2f}%)</span>
      </div>
    </div>
    <div>
      <div style="color:#999; font-size:0.75em;">持仓盈亏</div>
      <div style="font-size:1.2em; font-weight:bold; color:{pnl_html_color};">{pnl_pct:+.2f}%</div>
    </div>
    <div>
      <div style="color:#999; font-size:0.75em;">量比</div>
      <div style="font-size:1.1em;">{snap.volume_ratio:.2f}x
        {"" if snap.vol_ratio_stale_minutes < 5 else f'<span style="color:#F39C12;font-size:0.7em;">({snap.vol_ratio_stale_minutes:.0f}min旧)</span>'}
      </div>
    </div>
  </div>

  <div style="background:#0a0a0a; border-radius:6px; padding:12px; margin-bottom:10px;">
    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:6px;">
      <div style="color:#E0E0E0; font-size:1em;">
        动能 MS = <b style="font-size:1.3em; color:{ms_bar_color};">{ms_result.ms:.0f}</b>
        <span style="color:#aaa; font-size:0.85em;">({ms_level_label(ms_result.ms)}) {mode_hint}</span>
      </div>
      <div style="color:{ms_bar_color}; font-size:1.4em; letter-spacing:2px;">{bar_str}</div>
    </div>
    <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:6px; font-size:0.8em; color:#bbb; margin-bottom:8px;">
      <div>V量能={ms_result.v_score:.0f}</div>
      <div>P价格={ms_result.p_score:.0f}</div>
      <div>B盘口={ms_result.b_score:.0f}</div>
    </div>
    <div style="color:#ECECE0; font-size:1em; margin-bottom:6px;">{ms_result.action_text}</div>
    {f'<div style="color:#F39C12; font-size:0.9em;">{est_sell_str}</div>' if est_sell_str else ''}
  </div>

  <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; font-size:0.85em;">
    <div>
      <span style="color:#aaa;">移动止盈线</span><br/>
      <b style="color:#3498DB;">¥{ms_result.trail_stop_price:.3f}</b>
      {"" if ms_result.trail_stop_price <= 0 else f'<span style="color:#aaa; font-size:0.8em;">({(ms_result.trail_stop_price/cur-1)*100:+.1f}%)</span>' if cur > 0 else ""}
    </div>
    <div>
      <span style="color:#aaa;">硬止损价</span><br/>
      <b style="color:#E74C3C;">¥{ms_result.hard_stop_price:.3f}</b>
    </div>
    <div>
      <span style="color:#aaa;">ATR(5)</span><br/>
      <b>{ms_result.atr:.3f}</b>
    </div>
  </div>

  <div style="color:#666; font-size:0.7em; margin-top:8px; text-align:right;">
    今日区间 {snap.low_today:.3f} ~ {snap.high_today:.3f} | 换手 {snap.turnover:.2f}% | 
    {"⬆️涨停" if snap.is_limit_up else "⬇️跌停" if snap.is_limit_down else "正常"}
  </div>
</div>
"""
    st.markdown(html, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
# 主渲染函数
# ══════════════════════════════════════════════════════════════

def render():
    st.title("📡 实时监控仪表盘")
    st.caption(
        "MS 动能评分（V×40%+P×35%+B×25%）+ ATR 移动止盈线 + 预计卖出价  |  "
        "数据源：AKShare → 新浪（三级兜底）|  持仓自动同步自模拟交易"
    )

    # ── 侧边栏控制 ────────────────────────────────────────────────────
    with st.sidebar:
        st.divider()
        st.subheader("⚙️ 监控设置")

        auto_refresh = st.toggle("自动刷新", value=True)
        interval = st.select_slider(
            "刷新间隔(秒)",
            options=[10, 15, 30, 60, 120],
            value=_refresh_interval(),
            help="交易时段建议30~60秒，非交易时段自动减慢"
        )

        atr_k = st.slider(
            "ATR 倍数 K（Chandelier Exit）",
            min_value=1.0, max_value=4.0, value=2.0, step=0.5,
            help="止盈线 = 最高价 - K×ATR(5)，K越小止盈线越紧"
        )

        stop_loss_pct = st.slider(
            "硬止损比例 %",
            min_value=1.0, max_value=10.0, value=3.0, step=0.5,
            help="买入价 × (1-止损%) 以下强制止损"
        ) / 100.0

        st.divider()
        st.subheader("📌 手动添加持仓（测试）")
        manual_code = st.text_input("股票代码", placeholder="如 600519")
        manual_price = st.number_input("买入价", min_value=0.01, value=100.0, step=0.01)
        if st.button("➕ 添加测试持仓", use_container_width=True):
            if manual_code.strip():
                # 写入 session_state
                extra = st.session_state.get("extra_positions", [])
                code6 = str(manual_code.strip()).zfill(6)
                # 去重
                extra = [p for p in extra if p["code"] != code6]
                extra.append({
                    "code": code6,
                    "name": manual_code.strip(),
                    "buy_price": float(manual_price),
                    "trade_date": datetime.now().strftime("%Y%m%d"),
                    "id": -1,
                })
                st.session_state["extra_positions"] = extra
                st.success(f"已添加 {code6}（买入价 {manual_price}）")

        if st.button("🗑️ 清除测试持仓", use_container_width=True):
            st.session_state["extra_positions"] = []

    # ── 自动刷新 ──────────────────────────────────────────────────────
    if auto_refresh:
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=interval * 1000, key="realtime_autorefresh")
        except ImportError:
            st.caption(f"💡 提示：安装 `streamlit-autorefresh` 可启用 {interval}s 自动刷新")

    # ── 加载持仓 ──────────────────────────────────────────────────────
    db_positions = _load_open_positions()
    extra_positions = st.session_state.get("extra_positions", [])
    all_positions = db_positions + extra_positions

    if not all_positions:
        st.info(
            "📭 当前无持仓记录。\n\n"
            "**来源 A**：模拟炒股机器人 T+1 日自动买入后会生成持仓（status=open）\n\n"
            "**来源 B**：在左侧侧边栏手动输入股票代码和买入价进行测试"
        )
        # 非交易时段还是展示市场环境
        _render_market_env_section()
        return

    # ── 刷新按钮 + 时间戳 ────────────────────────────────────────────
    refresh_col, time_col = st.columns([1, 4])
    with refresh_col:
        manual_refresh = st.button("🔄 立即刷新", use_container_width=True)
    with time_col:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        market_status = "🟢 交易中" if _is_market_open() else "💤 休市"
        st.caption(f"刷新时间: {now_str}  |  状态: {market_status}  |  间隔: {interval}s")

    st.divider()

    # ── 批量获取行情 ──────────────────────────────────────────────────
    force_refresh = manual_refresh
    with st.spinner("获取实时行情..."):
        snap_map = fetch_snapshots_batch(
            all_positions,
            cache_ttl_sec=interval if not force_refresh else 0,
        )

    # ── 最高价追踪（从 session_state 中读取/更新）────────────────────
    if "highest_since_buy" not in st.session_state:
        st.session_state["highest_since_buy"] = {}

    for pos in all_positions:
        code = pos["code"]
        snap = snap_map.get(code)
        if snap and snap.current > 0:
            old_high = st.session_state["highest_since_buy"].get(code, snap.buy_price)
            new_high = max(old_high, snap.current)
            st.session_state["highest_since_buy"][code] = new_high

    # ── 数据质量统计 ──────────────────────────────────────────────────
    green_cnt = sum(1 for s in snap_map.values() if s.data_source in ("akshare_spot_em",))
    yellow_cnt = sum(1 for s in snap_map.values() if s.data_source == "sina_hq")
    red_cnt    = sum(1 for s in snap_map.values() if s.data_source == "none")

    dq1, dq2, dq3, dq4 = st.columns(4)
    dq1.metric("📊 监控持仓", len(all_positions))
    dq2.metric("🟢 正常数据", green_cnt)
    dq3.metric("🟡 备用数据", yellow_cnt)
    dq4.metric("🔴 数据断流", red_cnt)

    if red_cnt > 0:
        st.error(f"⚠️ {red_cnt} 只股票数据断流！已超过 2 分钟无法获取行情，请手动处理持仓！")
    elif yellow_cnt > 0:
        st.warning(f"🟡 {yellow_cnt} 只股票使用新浪备用数据源（量比可能不准确）")

    st.divider()

    # ── 渲染每只持仓卡片 ──────────────────────────────────────────────
    st.subheader(f"📌 当前持仓（{len(all_positions)} 只）")

    # 按 MS 分数从低到高排序（优先展示需要关注的）
    def _get_ms(pos):
        code = pos["code"]
        snap = snap_map.get(code)
        if snap is None or snap.current <= 0:
            return 50.0
        h = st.session_state["highest_since_buy"].get(code, snap.buy_price)
        r = calculate_ms(snap, highest_since_buy=h, atr_k=atr_k, stop_loss_pct=stop_loss_pct)
        return r.ms

    sorted_positions = sorted(all_positions, key=_get_ms)  # MS低的先展示

    for pos in sorted_positions:
        code = pos["code"]
        snap = snap_map.get(code)

        if snap is None:
            snap = MarketSnapshot()
            snap.code = code
            snap.buy_price = pos.get("buy_price", 0.0)

        if snap.buy_price <= 0 and pos.get("buy_price", 0) > 0:
            snap.buy_price = pos["buy_price"]

        highest = st.session_state["highest_since_buy"].get(code, snap.buy_price or snap.current)

        ms_result = calculate_ms(
            snap,
            highest_since_buy=highest,
            atr_k=atr_k,
            stop_loss_pct=stop_loss_pct,
        )

        _render_position_card(pos, snap, ms_result, highest)

    st.divider()

    # ── 市场环境区块 ──────────────────────────────────────────────────
    _render_market_env_section()

    # ── 操作建议汇总 ──────────────────────────────────────────────────
    _render_action_summary(all_positions, snap_map, atr_k, stop_loss_pct)


def _render_market_env_section():
    """渲染市场整体环境（涨停/跌停/上证）"""
    with st.expander("🌡️ 市场环境", expanded=False):
        with st.spinner("加载市场数据..."):
            env = _load_market_env()

        e1, e2, e3 = st.columns(3)
        pct_color = "normal" if env["sh_pct"] >= 0 else "inverse"
        e1.metric("上证综指", f"{env['sh_close']:.2f}",
                  delta=f"{env['sh_pct']:+.2f}%", delta_color=pct_color)
        e2.metric("涨停家数", f"{env['limit_up']} 家",
                  delta_color="normal" if env["limit_up"] > 30 else "off")
        e3.metric("跌停家数", f"{env['limit_down']} 家",
                  delta_color="inverse" if env["limit_down"] > 5 else "off")

        # 市场温度简评
        lu, ld = env["limit_up"], env["limit_down"]
        if lu > 50 and ld < 5:
            temp = "🔥 市场偏热，主力活跃，适合继续持有"
        elif lu < 10 and ld > 10:
            temp = "❄️ 市场偏冷，谨慎操作，适当减仓"
        elif lu > 30:
            temp = "🌤️ 市场中性偏暖，正常操作"
        else:
            temp = "🌥️ 市场中性，正常操作"
        st.info(f"市场温度：{temp}")


def _render_action_summary(positions, snap_map, atr_k, stop_loss_pct):
    """渲染操作建议汇总表"""
    rows = []
    for pos in positions:
        code = pos["code"]
        snap = snap_map.get(code)
        if snap is None:
            snap = MarketSnapshot()
            snap.code = code
            snap.buy_price = pos.get("buy_price", 0.0)
        if snap.buy_price <= 0 and pos.get("buy_price", 0) > 0:
            snap.buy_price = pos["buy_price"]

        highest = st.session_state.get("highest_since_buy", {}).get(code, snap.buy_price or snap.current)
        ms_r = calculate_ms(snap, highest_since_buy=highest, atr_k=atr_k, stop_loss_pct=stop_loss_pct)

        pnl = (snap.current - snap.buy_price) / snap.buy_price * 100 if snap.buy_price > 0 else 0.0

        rows.append({
            "代码":       code,
            "名称":       pos.get("name", code),
            "当前价":     round(snap.current, 3),
            "盈亏%":      round(pnl, 2),
            "MS评分":     ms_r.ms,
            "量能V":      ms_r.v_score,
            "价格P":      ms_r.p_score,
            "盘口B":      ms_r.b_score,
            "止盈线":     ms_r.trail_stop_price,
            "止损线":     ms_r.hard_stop_price,
            "预计卖出价": ms_r.est_sell_price if ms_r.est_sell_price > 0 else "—",
            "建议":       ms_r.action_text[:20] + "..." if len(ms_r.action_text) > 20 else ms_r.action_text,
        })

    if rows:
        with st.expander("📋 操作建议汇总表", expanded=True):
            df_sum = pd.DataFrame(rows)
            st.dataframe(
                df_sum,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "盈亏%":   st.column_config.NumberColumn("盈亏%",   format="%.2f%%"),
                    "MS评分":  st.column_config.ProgressColumn("MS评分", format="%.0f", min_value=0, max_value=100),
                    "量能V":   st.column_config.NumberColumn("量能V",   format="%.0f"),
                    "价格P":   st.column_config.NumberColumn("价格P",   format="%.0f"),
                    "盘口B":   st.column_config.NumberColumn("盘口B",   format="%.0f"),
                },
            )
