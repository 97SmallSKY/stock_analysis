"""
Celery 任务入口

所有定时任务的统一入口，通过 Celery beat 调度

任务列表：
- phase_a_task: 18:50 执行选股
- phase_b_task: 09:35 执行买入确认
- settlement_task: 15:05 执行每日结算
- ahs_task: 19:00 更新AHS评分
- monitor_task: 盘中每10秒监控（需常驻进程）

参考：docs/lhb-quant-v2/TRADING_FLOW.md §一
"""
from datetime import date
from typing import Optional

from celery import Celery, shared_task
from loguru import logger

from backend import run_phase_a
from backend import run_phase_b
from backend import run_daily_settlement
from backend import calculate_ahs_for_robot
from backend import run_monitor
from backend import check_circuit_breaker
from backend import send_alert, AlertLevel


# ============================================================
# Celery 配置
# ============================================================
app = Celery(
    'stock_strategy',
    broker='redis://localhost:6379/1',  # 使用Redis作为消息代理
    backend='redis://localhost:6379/1',  # 结果存储
)

# 从环境变量读取配置
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Shanghai',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5分钟超时
    task_soft_time_limit=240,  # 4分钟软超时
)


# ============================================================
# 任务定义
# ============================================================
@shared_task(bind=True, max_retries=3)
def phase_a_task(self, robot_id: int, signal_date: Optional[str] = None) -> dict:
    """
    Phase A 选股任务
    
    执行时间：交易日 18:50
    
    Args:
        robot_id: 机器人ID
        signal_date: 信号日期（YYYY-MM-DD格式，默认今天）
    
    Returns:
        执行结果
    """
    try:
        from backend import get_db_session
        
        # 解析日期
        trade_date = date.fromisoformat(signal_date) if signal_date else date.today()
        
        logger.info(f"[Celery] Phase A 任务开始: robot_id={robot_id}, date={trade_date}")
        
        # 检查熔断
        db = next(get_db_session())
        allowed, reason = await check_circuit_breaker(db, robot_id)
        if not allowed:
            logger.warning(f"[Celery] Phase A 被熔断阻止: {reason}")
            return {
                'success': False,
                'error': f'熔断: {reason}',
                'robot_id': robot_id,
                'date': str(trade_date)
            }
        
        # 执行 Phase A
        result = await run_phase_a(db, robot_id, trade_date)
        
        return {
            'success': result.success,
            'robot_id': robot_id,
            'date': str(trade_date),
            'selected_count': result.selected_count,
            'rejected_count': result.rejected_count,
            'ahs_blocked': result.ahs_blocked,
            'error': result.error_message,
        }
        
    except Exception as e:
        logger.exception(f"[Celery] Phase A 任务失败: {e}")
        
        # 重试
        try:
            raise self.retry(exc=e, countdown=60)  # 1分钟后重试
        except:
            pass
        
        # 发送告警
        await send_alert(
            title='Phase A 选股任务失败',
            content=f"robot_id={robot_id}, error={str(e)}",
            level=AlertLevel.ERROR
        )
        
        return {
            'success': False,
            'error': str(e),
            'robot_id': robot_id,
            'date': signal_date
        }


@shared_task(bind=True, max_retries=3)
def phase_b_task(self, robot_id: int, trade_date: Optional[str] = None) -> dict:
    """
    Phase B 确认买入任务
    
    执行时间：交易日 09:35（模拟盘）/ 09:45（实盘）
    
    Args:
        robot_id: 机器人ID
        trade_date: 交易日期（默认今天）
    
    Returns:
        执行结果
    """
    try:
        from backend import get_db_session
        
        trade_date_obj = date.fromisoformat(trade_date) if trade_date else date.today()
        
        logger.info(f"[Celery] Phase B 任务开始: robot_id={robot_id}, date={trade_date_obj}")
        
        db = next(get_db_session())
        
        # 执行 Phase B
        result = await run_phase_b(db, robot_id, trade_date_obj)
        
        return result
        
    except Exception as e:
        logger.exception(f"[Celery] Phase B 任务失败: {e}")
        
        try:
            raise self.retry(exc=e, countdown=60)
        except:
            pass
        
        await send_alert(
            title='Phase B 买入任务失败',
            content=f"robot_id={robot_id}, error={str(e)}",
            level=AlertLevel.ERROR
        )
        
        return {
            'success': False,
            'error': str(e),
            'robot_id': robot_id
        }


@shared_task(bind=True, max_retries=3)
def settlement_task(self, robot_id: int, settle_date: Optional[str] = None) -> dict:
    """
    每日结算任务
    
    执行时间：交易日 15:05
    
    Args:
        robot_id: 机器人ID
        settle_date: 结算日期（默认今天）
    
    Returns:
        执行结果
    """
    try:
        from backend import get_db_session
        
        settle_date_obj = date.fromisoformat(settle_date) if settle_date else date.today()
        
        logger.info(f"[Celery] 结算任务开始: robot_id={robot_id}, date={settle_date_obj}")
        
        db = next(get_db_session())
        
        # 执行结算
        result = await run_daily_settlement(db, robot_id, settle_date_obj)
        
        return {
            'success': True,
            'robot_id': robot_id,
            'date': str(settle_date_obj),
            'total_nav': result.total_nav,
            'daily_pnl': result.daily_pnl,
            'daily_pnl_ratio': result.daily_pnl_ratio,
            'position_count': result.position_count,
            'ahs_triggered': result.ahs_triggered,
        }
        
    except Exception as e:
        logger.exception(f"[Celery] 结算任务失败: {e}")
        
        try:
            raise self.retry(exc=e, countdown=60)
        except:
            pass
        
        await send_alert(
            title='每日结算任务失败',
            content=f"robot_id={robot_id}, error={str(e)}",
            level=AlertLevel.ERROR
        )
        
        return {
            'success': False,
            'error': str(e),
            'robot_id': robot_id
        }


@shared_task(bind=True, max_retries=3)
def ahs_task(self, robot_id: int, calc_date: Optional[str] = None) -> dict:
    """
    AHS 评分更新任务
    
    执行时间：每日 19:00
    
    Args:
        robot_id: 机器人ID
        calc_date: 计算日期（默认今天）
    
    Returns:
        执行结果
    """
    try:
        from backend import get_db_session
        
        calc_date_obj = date.fromisoformat(calc_date) if calc_date else date.today()
        
        logger.info(f"[Celery] AHS 任务开始: robot_id={robot_id}, date={calc_date_obj}")
        
        db = next(get_db_session())
        
        # 计算AHS
        result = await calculate_ahs_for_robot(db, robot_id, calc_date_obj)
        
        return {
            'success': True,
            'robot_id': robot_id,
            'date': str(calc_date_obj),
            'ahs_score': result.ahs_score,
            'trading_mode': result.trading_mode,
            'position_mult': result.position_mult,
        }
        
    except Exception as e:
        logger.exception(f"[Celery] AHS 任务失败: {e}")
        
        try:
            raise self.retry(exc=e, countdown=60)
        except:
            pass
        
        await send_alert(
            title='AHS 更新任务失败',
            content=f"robot_id={robot_id}, error={str(e)}",
            level=AlertLevel.ERROR
        )
        
        return {
            'success': False,
            'error': str(e),
            'robot_id': robot_id
        }


@shared_task(bind=True, max_retries=0)  # 不自动重试
def monitor_task(self, robot_id: int) -> dict:
    """
    盘中监控任务
    
    执行频率：每10秒（需常驻进程）
    
    Args:
        robot_id: 机器人ID
    
    Returns:
        执行结果
    """
    try:
        from backend import get_db_session
        
        logger.debug(f"[Celery] 监控任务: robot_id={robot_id}")
        
        db = next(get_db_session())
        
        # 执行监控
        result = await run_monitor(db, robot_id)
        
        return {
            'success': True,
            'robot_id': robot_id,
            'checked_count': result.checked_count,
            'take_profit_count': result.take_profit_count,
            'stop_loss_count': result.stop_loss_count,
            'expired_count': result.expired_count,
        }
        
    except Exception as e:
        logger.exception(f"[Celery] 监控任务失败: {e}")
        
        await send_alert(
            title='盘中监控任务失败',
            content=f"robot_id={robot_id}, error={str(e)}",
            level=AlertLevel.ERROR
        )
        
        return {
            'success': False,
            'error': str(e),
            'robot_id': robot_id
        }


# ============================================================
# Celery Beat 调度配置
# ============================================================
app.conf.beat_schedule = {
    # Phase A: 18:50
    'phase-a-daily': {
        'task': 'backend.app.services.strategy.tasks.phase_a_task',
        'schedule': 18 * 60 + 50,  # 18:50 (分钟数)
        'options': {'expires': 3600}  # 1小时后过期
    },
    
    # Phase B: 09:35
    'phase-b-daily': {
        'task': 'backend.app.services.strategy.tasks.phase_b_task',
        'schedule': 9 * 60 + 35,  # 09:35
        'options': {'expires': 3600}
    },
    
    # 结算: 15:05
    'settlement-daily': {
        'task': 'backend.app.services.strategy.tasks.settlement_task',
        'schedule': 15 * 60 + 5,  # 15:05
        'options': {'expires': 3600}
    },
    
    # AHS: 19:00
    'ahs-daily': {
        'task': 'backend.app.services.strategy.tasks.ahs_task',
        'schedule': 19 * 60,  # 19:00
        'options': {'expires': 3600}
    },
    
    # 监控: 每10秒
    'monitor-every-10s': {
        'task': 'backend.app.services.strategy.tasks.monitor_task',
        'schedule': 10.0,  # 10秒
    },
}

# 时区设置
app.conf.timezone = 'Asia/Shanghai'
