"""
自动化监控调度模块
- 盘后 18:30 自动扫描龙虎榜
- 早盘 09:20 集合竞价检查
- 盘中每分钟止盈止损监控
- 支持邮件/钉钉/Server酱通知
"""
import time
import schedule
import requests
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from loguru import logger

import akshare as ak
import pandas as pd

from config.settings import STRATEGY_CONFIG, NOTIFY_CONFIG, FAMOUS_TRADERS
from modules.data_fetcher import fetch_lhb_detail, fetch_market_sentiment
from modules.analyzer import select_stocks_daily
from utils.storage import storage


class LHBMonitor:
    """龙虎榜自动监控系统"""

    def __init__(self, config: dict = None):
        self.config = config or {**STRATEGY_CONFIG, **NOTIFY_CONFIG}
        self.selected_stocks = []   # 当日选出的股票
        self.positions = {}         # 当前持仓 {code: {name, buy_price, buy_date}}
        self.trade_log = []         # 交易记录

    # ============================================================
    # 盘后扫描（每天 18:30）
    # ============================================================
    def evening_scan(self):
        """盘后自动扫描龙虎榜，输出明日候选"""
        today = datetime.now().strftime("%Y%m%d")
        logger.info(f"\n{'='*60}\n📡 {today} 盘后龙虎榜扫描\n{'='*60}")

        # 1. 市场情绪检查
        sentiment = fetch_market_sentiment(today, save=True)
        trade_signal = sentiment.get("trade_signal", "normal")
        reason = sentiment.get("signal_reason", "")

        logger.info(
            f"市场情绪: 涨停={sentiment.get('limit_up', '?')}, "
            f"跌停={sentiment.get('limit_down', '?')}, "
            f"大盘={sentiment.get('index_trend', '?')}"
        )

        if trade_signal == "no_trade":
            msg = f"⚠️ {today} {reason}，明日不操作"
            logger.warning(msg)
            self.send_notification(msg)
            return

        # 2. 拉取龙虎榜
        try:
            lhb_df = fetch_lhb_detail(today, today, save=True)
            if lhb_df is None or lhb_df.empty:
                logger.info("今日无龙虎榜数据")
                return
        except Exception as e:
            logger.error(f"获取龙虎榜失败: {e}")
            return

        # 3. 选股
        selected = select_stocks_daily(
            lhb_df, today,
            top_n=self.config.get("top_n", 3),
            min_net_buy=self.config.get("min_net_buy", 50_000_000),
            min_tech_score=self.config.get("min_tech_score", 50),
        )

        # 4. 保存 & 通知
        self.selected_stocks = selected

        if not selected:
            msg = f"📊 {today} 龙虎榜扫描完毕，无符合条件标的"
            logger.info(msg)
            self.send_notification(msg)
            return

        lines = [f"🎯 {today} 龙虎榜选股结果:\n"]
        for i, s in enumerate(selected, 1):
            line = (
                f"{i}. {s.get('name', '')}({s.get('code', '')})\n"
                f"   净买入: {s.get('net_buy', 0)/1e8:.2f}亿  "
                f"席位:{s.get('seat_score', 0)}  "
                f"技术:{s.get('tech_score', 0)}  "
                f"综合:{s.get('final_score', 0):.1f}\n"
                f"   机构买入: {'✅' if s.get('has_institution') else '❌'}  "
                f"20日涨幅: {s.get('gain_20d', 0):.1%}\n"
            )
            lines.append(line)

        pos_note = "⚠️ 半仓操作" if trade_signal == "half_position" else "✅ 正常仓位"
        lines.append(f"\n{pos_note}")

        full_msg = "\n".join(lines)
        logger.info(full_msg)
        self.send_notification(full_msg)

        # 保存选股结果
        result_df = pd.DataFrame(selected)
        result_df["select_date"] = today
        storage.write(result_df, "selected_stocks", mode="append")

    # ============================================================
    # 早盘检查（每天 09:20）
    # ============================================================
    def morning_check(self):
        """早盘集合竞价检查"""
        if not self.selected_stocks:
            return

        logger.info("⏰ 集合竞价检查")
        lines = ["⏰ 集合竞价检查:\n"]

        for stock in self.selected_stocks:
            code = stock["code"]
            try:
                spot_df = ak.stock_zh_a_spot_em()
                row = spot_df[spot_df["代码"] == code]

                if row.empty:
                    continue

                pre_close = float(row.iloc[0].get("昨收", 0))
                current = float(row.iloc[0].get("最新价", 0))

                if pre_close == 0:
                    continue

                gap = (current / pre_close - 1)

                if gap > 0.07:
                    action = "❌ 高开过多，放弃"
                elif gap > 0.05:
                    action = "⚠️ 高开>5%，减半仓"
                elif gap > 0.02:
                    action = "✅ 适度高开，正常买入"
                elif gap > -0.02:
                    action = "✅ 平开，正常买入"
                else:
                    action = "🔍 低开，观察后决定"

                line = f"  {stock.get('name', '')}({code}): {current:.2f} 涨幅{gap:.2%} → {action}"
                lines.append(line)
                logger.info(line)

            except Exception as e:
                logger.warning(f"  {code} 获取竞价数据失败: {e}")

        self.send_notification("\n".join(lines))

    # ============================================================
    # 盘中监控（每分钟）
    # ============================================================
    def intraday_monitor(self):
        """盘中止盈止损监控"""
        if not self.positions:
            return

        now = datetime.now()
        # 只在 9:30~15:00 运行
        if not (9 * 60 + 30 <= now.hour * 60 + now.minute <= 15 * 60):
            return

        alerts = []
        for code, pos in list(self.positions.items()):
            try:
                spot_df = ak.stock_zh_a_spot_em()
                row = spot_df[spot_df["代码"] == code]
                if row.empty:
                    continue

                current = float(row.iloc[0].get("最新价", 0))
                buy_price = pos["buy_price"]
                pnl = (current - buy_price) / buy_price

                if pnl <= self.config.get("stop_loss", -0.05):
                    msg = f"🔴 止损! {pos.get('name', '')}({code}) 亏损{pnl:.2%}"
                    alerts.append(msg)
                    logger.warning(msg)

                elif pnl >= self.config.get("stop_profit", 0.08):
                    msg = f"🟢 止盈! {pos.get('name', '')}({code}) 盈利{pnl:.2%}"
                    alerts.append(msg)
                    logger.info(msg)

            except Exception as e:
                logger.debug(f"{code} 监控失败: {e}")

        if alerts:
            self.send_notification("\n".join(alerts))

    # ============================================================
    # 通知发送
    # ============================================================
    def send_notification(self, message: str):
        """发送通知（邮件/钉钉/Server酱）"""
        sent = False

        # 邮件
        try:
            from_addr = self.config.get("email_from", "")
            to_addr = self.config.get("email_to", "")
            pwd = self.config.get("email_password", "")

            if from_addr and to_addr and pwd:
                msg = MIMEText(message, "plain", "utf-8")
                msg["Subject"] = "【龙虎榜策略通知】"
                msg["From"] = from_addr
                msg["To"] = to_addr

                with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=10) as server:
                    server.login(from_addr, pwd)
                    server.send_message(msg)
                sent = True
                logger.debug("邮件通知发送成功")
        except Exception as e:
            logger.debug(f"邮件通知失败: {e}")

        # 钉钉机器人
        try:
            webhook = self.config.get("dingtalk_webhook", "")
            if webhook:
                resp = requests.post(
                    webhook,
                    json={"msgtype": "text", "text": {"content": f"【龙虎榜策略】\n{message}"}},
                    timeout=5,
                )
                if resp.status_code == 200:
                    sent = True
                    logger.debug("钉钉通知发送成功")
        except Exception as e:
            logger.debug(f"钉钉通知失败: {e}")

        # Server酱
        try:
            sckey = self.config.get("server_chan_key", "")
            if sckey:
                requests.get(
                    f"https://sctapi.ftqq.com/{sckey}.send",
                    params={"title": "龙虎榜通知", "desp": message},
                    timeout=5,
                )
                sent = True
                logger.debug("Server酱通知发送成功")
        except Exception as e:
            logger.debug(f"Server酱通知失败: {e}")

        if not sent:
            logger.info(f"[通知内容]\n{message}")


# ============================================================
# 调度入口
# ============================================================

def run_monitor(config: dict = None):
    """启动自动化监控（阻塞运行）"""
    monitor = LHBMonitor(config)

    schedule.every().day.at("18:30").do(monitor.evening_scan)
    schedule.every().day.at("09:20").do(monitor.morning_check)
    schedule.every(1).minutes.do(monitor.intraday_monitor)

    logger.info("🚀 龙虎榜监控系统已启动")
    logger.info("  - 18:30 盘后选股扫描")
    logger.info("  - 09:20 早盘集合竞价检查")
    logger.info("  - 盘中每分钟止盈止损监控")
    logger.info("按 Ctrl+C 停止")

    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("监控系统已停止")


if __name__ == "__main__":
    import sys
    
    # 支持立即触发测试
    if len(sys.argv) > 1 and sys.argv[1] == "--scan-now":
        monitor = LHBMonitor()
        monitor.evening_scan()
    else:
        run_monitor()
