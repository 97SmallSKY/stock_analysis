"""
飞书通知封装

所有业务通知统一走此类。
FEISHU_WEBHOOK_URL 从环境变量读取，为空时静默跳过（不报错）。

通知类型：
1. 开仓通知 - send_trade_open
2. 平仓通知 - send_trade_close
3. 每日日报 - send_daily_report
4. 告警通知 - send_alert

参考：docs/lhb-quant-v2/TRADING_FLOW.md §六
"""
import os
import asyncio
from datetime import datetime, date
from typing import Optional
from dataclasses import dataclass
from enum import Enum

from loguru import logger
import httpx


class AlertLevel(str, Enum):
    """告警级别"""
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'
    CRITICAL = 'critical'


@dataclass
class CloseReason:
    """平仓原因"""
    value: str
    
    @classmethod
    def stop_loss(cls):
        return cls('止损')
    
    @classmethod
    def take_profit(cls, tier: int):
        return cls(f'止盈{tier}档')
    
    @classmethod
    def expire(cls):
        return cls('到期平仓')
    
    @classmethod
    def lianban_open(cls):
        return cls('连板开板')
    
    @classmethod
    def manual(cls):
        return cls('手动平仓')


class FeishuNotifier:
    """
    飞书 Webhook 消息发送封装
    """
    
    def __init__(self, webhook_url: Optional[str] = None):
        """
        初始化
        
        Args:
            webhook_url: Webhook URL（为空时从环境变量读取）
        """
        self.webhook_url = webhook_url or os.environ.get('FEISHU_WEBHOOK_URL', '')
        
        if not self.webhook_url:
            logger.warning("[Feishu] Webhook URL 未配置，通知将被静默跳过")
    
    async def send(self, content: str) -> bool:
        """
        发送消息
        
        Args:
            content: 消息内容（纯文本）
        
        Returns:
            True 表示发送成功
        """
        if not self.webhook_url:
            logger.debug(f"[Feishu] 跳过发送: {content[:50]}...")
            return False
        
        payload = {
            "msg_type": "text",
            "content": {
                "text": content
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload
                )
                
                if response.status_code != 200:
                    logger.error(f"[Feishu] 发送失败: {response.status_code} - {response.text}")
                    return False
                
                result = response.json()
                if result.get('code', 0) != 0:
                    logger.error(f"[Feishu] 发送失败: {result}")
                    return False
                
                logger.debug("[Feishu] 发送成功")
                return True
                
        except Exception as e:
            logger.error(f"[Feishu] 发送异常: {e}")
            return False
    
    async def send_trade_open(
        self,
        robot_name: str,
        stock_code: str,
        stock_name: str,
        shares: int,
        price: float,
        score: float
    ) -> None:
        """
        开仓通知
        
        模板：
        📈 开仓通知
        机器人：{robot_name}
        股票：{stock_code} {stock_name}
        数量：{shares} 股 @ ¥{price}
        评分：{score:.1f} 分
        时间：{now}
        """
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        content = (
            f"📈 开仓通知\n"
            f"机器人：{robot_name}\n"
            f"股票：{stock_code} {stock_name}\n"
            f"数量：{shares} 股 @ ¥{price:.3f}\n"
            f"评分：{score:.1f} 分\n"
            f"时间：{now}"
        )
        
        await self.send(content)
    
    async def send_trade_close(
        self,
        robot_name: str,
        stock_code: str,
        stock_name: str,
        pnl: float,
        pnl_ratio: float,
        reason: str
    ) -> None:
        """
        平仓通知
        
        模板（盈利用 📈，亏损用 📉）：
        📉 平仓通知
        机器人：{robot_name}
        股票：{stock_code} {stock_name}
        盈亏：{+pnl:.2f} 元 ({+pnl_ratio:.2%})
        原因：{reason.value}
        """
        icon = "📈" if pnl >= 0 else "📉"
        content = (
            f"{icon} 平仓通知\n"
            f"机器人：{robot_name}\n"
            f"股票：{stock_code} {stock_name}\n"
            f"盈亏：{pnl:+.2f} 元 ({pnl_ratio:+.2%})\n"
            f"原因：{reason}"
        )
        
        await self.send(content)
    
    async def send_daily_report(
        self,
        robot_name: str,
        nav: float,
        daily_return: float,
        open_count: int,
        ahs_score: float
    ) -> None:
        """
        每日结算报告（15:00 后发送）
        
        模板：
        📊 每日结算报告 {date}
        机器人：{robot_name}
        净值：{nav:.4f} (今日 {+daily_return:.2%})
        持仓：{open_count} 只
        AHS：{ahs_score:.1f} ({risk_level})
        """
        today = date.today().strftime('%Y-%m-%d')
        
        # 根据 AHS 分数判断风险等级
        if ahs_score <= 20:
            risk_level = "正常"
        elif ahs_score <= 40:
            risk_level = "谨慎"
        elif ahs_score <= 60:
            risk_level = "保守"
        elif ahs_score <= 80:
            risk_level = "警戒"
        else:
            risk_level = "停仓"
        
        content = (
            f"📊 每日结算报告 {today}\n"
            f"机器人：{robot_name}\n"
            f"净值：{nav:.4f} (今日 {daily_return:+.2%})\n"
            f"持仓：{open_count} 只\n"
            f"AHS：{ahs_score:.1f} ({risk_level})"
        )
        
        await self.send(content)
    
    async def send_alert(
        self,
        title: str,
        content: str,
        level: AlertLevel = AlertLevel.WARNING
    ) -> None:
        """
        通用告警
        
        模板：
        ⚠️ [{level}] {title}
        {content}
        """
        level_icons = {
            AlertLevel.INFO: 'ℹ️',
            AlertLevel.WARNING: '⚠️',
            AlertLevel.ERROR: '❌',
            AlertLevel.CRITICAL: '🚨',
        }
        
        icon = level_icons.get(level, '⚠️')
        message = f"{icon} [{level.value.upper()}] {title}\n{content}"
        
        await self.send(message)


# ============================================================
# 全局实例和便捷函数
# ============================================================
_notifier: Optional[FeishuNotifier] = None


def get_notifier() -> FeishuNotifier:
    """获取全局通知器实例"""
    global _notifier
    if _notifier is None:
        _notifier = FeishuNotifier()
    return _notifier


async def send_trade_open(
    robot_name: str,
    stock_code: str,
    stock_name: str,
    shares: int,
    price: float,
    score: float
) -> None:
    """发送开仓通知"""
    await get_notifier().send_trade_open(
        robot_name, stock_code, stock_name, shares, price, score
    )


async def send_trade_close(
    robot_name: str,
    stock_code: str,
    stock_name: str,
    pnl: float,
    pnl_ratio: float,
    reason: str
) -> None:
    """发送平仓通知"""
    await get_notifier().send_trade_close(
        robot_name, stock_code, stock_name, pnl, pnl_ratio, reason
    )


async def send_daily_report(
    robot_name: str,
    nav: float,
    daily_return: float,
    open_count: int,
    ahs_score: float
) -> None:
    """发送每日日报"""
    await get_notifier().send_daily_report(
        robot_name, nav, daily_return, open_count, ahs_score
    )


async def send_alert(
    title: str,
    content: str,
    level: AlertLevel = AlertLevel.WARNING
) -> None:
    """发送告警通知"""
    await get_notifier().send_alert(title, content, level)


# ============================================================
# 同步版本（用于脚本等场景）
# ============================================================
def send_alert_sync(title: str, content: str, level: AlertLevel = AlertLevel.WARNING) -> None:
    """同步发送告警"""
    asyncio.run(send_alert(title, content, level))
