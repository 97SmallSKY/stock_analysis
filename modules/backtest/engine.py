"""
BacktestEngine — 统一回测引擎
支持事件驱动（event）和全时序（bar）两种模式的单股回测
"""
import pandas as pd
import numpy as np
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from loguru import logger

from modules.backtest.data_feed import DataFeed, _norm_date
from modules.backtest.executor import Executor, BacktestContext
from modules.backtest.portfolio import Position, Bar
from modules.backtest import metrics as bt_metrics
from modules.strategies.base import BaseStrategy, Signal, BuyOrder
from utils.storage import storage


@dataclass
class BacktestDiagnostic:
    """回测诊断信息：让上层区分'真无信号' / '全被过滤' / '数据缺失'"""
    kline_rows: int = 0          # 加载的 K 线行数
    signals_total: int = 0       # scan_signals 产出数量
    trades_total: int = 0        # 实际成交数
    skip_stats: dict = field(default_factory=dict)  # 跳过原因 → 次数
    notes: list = field(default_factory=list)        # 额外提示（策略层填充）

    @property
    def empty_reason(self) -> str:
        """当 trades_total==0 时给出人类可读的原因"""
        if self.kline_rows == 0:
            return "数据库无 K 线数据"
        if self.signals_total == 0:
            base = f"K 线 {self.kline_rows} 行，但策略未产生信号"
            if self.notes:
                return f"{base}（{'；'.join(self.notes)}）"
            return f"{base}（参数过严或区间过短）"
        if self.trades_total == 0:
            detail = "、".join(f"{k}:{v}" for k, v in self.skip_stats.items()) if self.skip_stats else "未知"
            return f"产生 {self.signals_total} 个信号但全部被过滤（{detail}）"
        return ""


def _code_matches(a: str, b: str) -> bool:
    """两个股票代码是否指向同一只股票（容错 6 位 / ts_code / 是否带后缀）"""
    def _clean(c):
        return str(c).replace(".SZ", "").replace(".SH", "").replace(".BJ", "").zfill(6)
    return _clean(a) == _clean(b)


def _strategy_overrides(strategy, method_name: str) -> bool:
    """检测策略类是否真的覆写了某方法（不是用 BaseStrategy 默认实现）"""
    base_fn = getattr(BaseStrategy, method_name, None)
    sub_fn = getattr(type(strategy), method_name, None)
    return base_fn is not None and sub_fn is not None and sub_fn is not base_fn


class BacktestEngine:
    """单股回测引擎"""

    def __init__(self, data_feed: DataFeed = None, roundtrip_cost: float = 0.0025):
        self.data_feed = data_feed or DataFeed()
        self.executor = Executor(self.data_feed, roundtrip_cost=roundtrip_cost)

    def run(
        self, code: str, start: str, end: str,
        strategy: BaseStrategy, ctx: BacktestContext = None,
    ) -> tuple[pd.DataFrame, BacktestDiagnostic]:
        """
        执行单股回测。

        Args:
            code: 股票 ts_code (如 000001.SZ)
            start/end: 回测日期区间
            strategy: 策略实例
            ctx: 回测上下文（可选，自动创建）

        Returns:
            (DataFrame, BacktestDiagnostic) 元组。空 DataFrame 时通过 diag.empty_reason 判断原因。
        """
        diag = BacktestDiagnostic()

        if ctx is None:
            ctx = BacktestContext(
                data_feed=self.data_feed, code=code,
                start=start, end=end,
                strategy_params=strategy.params,
            )

        # 0. 数据可用性检查
        kline_df = self.data_feed.kline(code, start, end)
        diag.kline_rows = len(kline_df)
        if diag.kline_rows == 0:
            logger.warning(f"无 K 线数据: {code} {start}-{end}")
            return pd.DataFrame(), diag

        # 1. 策略准备
        if hasattr(strategy, 'prepare'):
            strategy.prepare(ctx)

        # 2. 扫描信号
        # 关键：BaseStrategy 自己声明了 scan_signals 默认实现，hasattr 永远为 True，
        # 不能靠它判断是否新策略。改用类层面的 override 检测。
        if _strategy_overrides(strategy, 'scan_signals'):
            signals = strategy.scan_signals(ctx)
        elif _strategy_overrides(strategy, 'select'):
            # 老策略（lhb_high_score / single_stock_event）：走 LHB 选股链路
            signals, legacy_notes = self._signals_from_legacy(code, start, end, strategy)
            diag.notes.extend(legacy_notes)
        else:
            signals = []
            diag.notes.append("策略未实现 scan_signals 或 select 方法")

        diag.signals_total = len(signals)
        if not signals:
            logger.info(f"无信号: {code} {start}-{end} (kline={diag.kline_rows})")
            return pd.DataFrame(), diag

        # 3. 构建 Exit 链
        exits = None
        if hasattr(strategy, 'build_exits'):
            exits = strategy.build_exits()

        # 4. 逐信号执行
        results = []
        skip_stats = {}
        last_sell_date = None
        no_overlap = not strategy.params.get("allow_overlap", True)

        for sig in signals:
            sig_date = sig.date if hasattr(sig, 'date') else sig.get("date", "")
            sig_code = sig.code if hasattr(sig, 'code') else sig.get("code", code)

            if no_overlap and last_sell_date and sig_date < last_sell_date:
                skip_stats["持仓重叠跳过"] = skip_stats.get("持仓重叠跳过", 0) + 1
                continue

            if exits is not None and hasattr(exits, 'reset'):
                exits.reset(sig_code)

            ret = self.executor.execute_trade(
                sig_code, sig_date, ctx, strategy=strategy, exits=exits,
            )
            if ret is None:
                skip_stats["K线获取失败"] = skip_stats.get("K线获取失败", 0) + 1
                continue
            if ret.get("skipped"):
                reason = ret.get("exit_reason", "未知")
                skip_stats[reason] = skip_stats.get(reason, 0) + 1
                continue

            sell_d = ret.get("sell_date", "")
            if sell_d and (last_sell_date is None or sell_d > last_sell_date):
                last_sell_date = sell_d

            meta = sig.meta if hasattr(sig, 'meta') else sig.get("meta", {})
            results.append({
                "trade_date": sig_date, "code": sig_code,
                "name": meta.get("name", sig_code),
                "signal_meta": meta,
                **{k: v for k, v in ret.items() if k != "skipped"},
            })

        df = pd.DataFrame(results)
        diag.trades_total = len(results)
        diag.skip_stats = skip_stats

        total_attempted = len(results) + sum(skip_stats.values())
        logger.info(
            f"回测完成: {code} {start}-{end} → 成交 {len(df)} / 尝试 {total_attempted} 笔；跳过明细: {skip_stats}"
        )
        return df, diag

    def run_batch(
        self, start: str, end: str, strategy: BaseStrategy,
        save: bool = True,
    ) -> tuple[pd.DataFrame, BacktestDiagnostic]:
        """批量回测（旧接口兼容）"""
        from modules.data_fetcher import load_lhb_detail

        diag = BacktestDiagnostic()
        self.data_feed.clear_cache()
        lhb_df = load_lhb_detail(start, end)
        if lhb_df.empty:
            logger.error("无龙虎榜数据")
            return pd.DataFrame(), diag

        date_col = "trade_date" if "trade_date" in lhb_df.columns else "上榜日期"
        trade_dates = sorted(lhb_df[date_col].astype(str).unique())

        # 预取 K 线
        all_codes = set()
        for _, r in lhb_df.iterrows():
            code_col = "代码" if "代码" in lhb_df.columns else "股票代码"
            all_codes.add(str(r.get(code_col, "")))
        self.data_feed.prefetch(list(all_codes)[:500], start, end)

        # 每日选股
        results = []
        skip_stats = {}
        signals_total = 0
        ctx = BacktestContext(
            data_feed=self.data_feed, start=start, end=end,
            strategy_params=strategy.params,
        )

        for date_str in trade_dates:
            if hasattr(strategy, 'select'):
                orders = strategy.select(date_str, lhb_df, self.data_feed._cache)
            else:
                continue
            signals_total += len(orders)

            for o in orders:
                code = o.code if hasattr(o, 'code') else o.get("code", "")
                ret = self.executor.execute_trade(code, date_str, ctx, strategy=strategy)
                if ret is None or ret.get("skipped"):
                    reason = ret.get("exit_reason", "跳过") if ret else "无数据"
                    skip_stats[reason] = skip_stats.get(reason, 0) + 1
                    continue
                meta = o.meta if hasattr(o, 'meta') else o.get("meta", {})
                results.append({
                    "trade_date": date_str, "code": code,
                    "name": meta.get("name", code),
                    "signal_meta": meta,
                    **{k: v for k, v in ret.items() if k != "skipped"},
                })

        df = pd.DataFrame(results)
        diag.signals_total = signals_total
        diag.trades_total = len(results)
        diag.skip_stats = skip_stats

        if save and not df.empty:
            ok = self._save_results(df, strategy.name, start, end)
            if not ok:
                logger.error("批量回测结果保存失败，请检查 PG 表 schema")
        return df, diag

    def _signals_from_legacy(self, code: str, start: str, end: str, strategy: BaseStrategy) -> tuple[list, list]:
        """旧策略兼容：用 select() 生成 Signal，并按目标 code 过滤（单股回测语义）。

        性能优化：先用 lhb_events(code) 拿到目标股上榜日（一般 3-30 个），仅在这些日期上调 select()，
        避免对全部交易日（500+）空跑。

        Returns:
            (signals, notes)：notes 用于回写诊断信息（"未上榜" / "上榜但未达评分门槛"）
        """
        notes: list[str] = []

        # 先快速查目标股的上榜日；空则直接返回，不必 load 全市场 LHB
        own_events = self.data_feed.lhb_events(code, start, end)
        if own_events.empty:
            notes.append(f"{code} 在区间内未上龙虎榜")
            logger.info(f"{code} 区间内无龙虎榜上榜记录，跳过 LHB 策略")
            return [], notes

        own_dates = set(
            own_events["trade_date"].astype(str).str.replace("-", "").str[:8].tolist()
        )

        from modules.data_fetcher import load_lhb_detail
        lhb_df = load_lhb_detail(start, end)
        if lhb_df.empty:
            notes.append("回测期内无龙虎榜全市场数据")
            return [], notes

        signals = []
        for date_str in sorted(own_dates):
            try:
                orders = strategy.select(date_str, lhb_df, self.data_feed._cache)
            except Exception as e:
                logger.debug(f"select() 失败 {date_str}: {e}")
                continue
            for o in orders:
                cd = o.code if hasattr(o, 'code') else o.get("code", "")
                # 单股回测：仅保留与目标代码匹配的信号
                if not _code_matches(cd, code):
                    continue
                meta = o.meta if hasattr(o, 'meta') else o.get("meta", {})
                signals.append(Signal(date=date_str, code=cd, meta=meta))

        if not signals and own_dates:
            notes.append(
                f"目标股区间内上榜 {len(own_dates)} 次，但均未通过策略评分门槛"
                f"（min_net_buy/min_tech_score/max_gain_20d）"
            )
        return signals, notes

    def _save_results(self, df: pd.DataFrame, strategy_name: str, start: str, end: str) -> bool:
        """保存结果到 backtest_results / backtest_runs。返回 True/False 表示是否成功"""
        run_id = uuid.uuid4().hex[:12]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        df_save = df.copy()
        df_save["run_id"] = run_id
        df_save["run_type"] = "batch"
        df_save["strategy_name"] = strategy_name
        df_save["created_at"] = now

        # 处理 signal_meta 列（JSON 序列化）
        if "signal_meta" in df_save.columns:
            import json
            df_save["signal_meta"] = df_save["signal_meta"].apply(
                lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else str(x)
            )

        ok_results = storage.write(df_save, "backtest_results", mode="append")

        run_meta = pd.DataFrame([{
            "run_id": run_id, "task_id": run_id,
            "strategy_name": strategy_name, "status": "SUCCESS",
            "start_date": start, "end_date": end,
            "trade_count": len(df_save),
            "config": "{}",
            "created_at": now,
        }])
        ok_runs = storage.write(run_meta, "backtest_runs", mode="append")

        if ok_results and ok_runs:
            logger.info(f"已保存 run={run_id} ({len(df_save)} 笔)")
            return True
        logger.error(f"保存失败 run={run_id}: results={ok_results} runs={ok_runs}")
        return False
