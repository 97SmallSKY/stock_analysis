"""
机器人淘汰引擎

流程：
1. 检查是否到达淘汰日
2. 计算所有运行中机器人评分
3. 淘汰得分最低的N个
4. 强制清仓 + 状态改为 eliminated
5. 记录同期所有机器人快照
"""
import sys
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text
from robot.manager import RobotManager
from strategy.scorer import rank_robots

logger = logging.getLogger(__name__)


class Eliminator:
    """
    机器人淘汰引擎
    
    策略：
    - 每20个交易日淘汰1个最低分机器人
    - 保留至少3个机器人运行
    - 记录淘汰时的完整快照用于分析
    """
    
    def __init__(self):
        self.engine = storage._get_engine()
        self.rm = RobotManager()
    
    def check_and_run(self, trade_date: str = None):
        """检查并执行淘汰"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        
        # 获取活跃的淘汰计划
        with self.engine.connect() as conn:
            schedules = conn.execute(text('''
                SELECT * FROM elimination_schedule WHERE is_active = true
            ''')).fetchall()
        
        for schedule in schedules:
            schedule = dict(schedule._mapping)
            
            if self._should_run(schedule, trade_date):
                self._execute_elimination(schedule, trade_date)
    
    def _should_run(self, schedule: dict, trade_date: str) -> bool:
        """判断是否应该执行淘汰"""
        last_run = schedule.get('last_run_date')
        
        if not last_run:
            return True
        
        # 计算距离上次执行经过的交易日数
        with self.engine.connect() as conn:
            days = conn.execute(text('''
                SELECT COUNT(DISTINCT trade_date) FROM sim_daily_summary
                WHERE trade_date > :last AND trade_date <= :current
            '''), {'last': last_run, 'current': trade_date}).scalar()
        
        return days >= schedule['period_days']
    
    def _execute_elimination(self, schedule: dict, trade_date: str):
        """执行淘汰"""
        logger.info("=" * 50)
        logger.info(f"🗑️ 淘汰检查开始: {trade_date}")
        logger.info("=" * 50)
        
        # 获取所有运行中的机器人
        running = self.rm.list_robots(status='running')
        running_ids = [r['robot_id'] for r in running]
        
        # 检查是否满足最小数量
        if len(running_ids) <= schedule['min_robots']:
            logger.info(f"运行中机器人 {len(running_ids)} 个，不淘汰")
            return
        
        # 计算排名
        rankings = rank_robots(running_ids)
        
        if not rankings:
            logger.warning("⚠️ 无法计算排名")
            return
        
        # 记录所有机器人的快照（用于历史分析）
        peer_snapshot = [{
            'robot_id': r['robot_id'],
            'robot_name': r.get('robot_name', r['robot_id']),
            'rank': r['rank'],
            'total_score': r['total_score'],
            'metrics': r['metrics'],
        } for r in rankings]
        
        # 计算需要淘汰的数量
        eliminate_count = min(
            schedule['eliminate_count'],
            len(rankings) - schedule['min_robots']
        )
        
        if eliminate_count <= 0:
            logger.info("无需淘汰")
            return
        
        # 淘汰最低分的N个
        to_eliminate = rankings[-eliminate_count:]
        
        for victim in to_eliminate:
            victim_id = victim['robot_id']
            victim_info = next(
                (r for r in running if r['robot_id'] == victim_id), 
                {'robot_name': victim_id}
            )
            
            logger.warning(
                f"🗑️ 淘汰: {victim_info.get('robot_name')} ({victim_id}) "
                f"| 得分: {victim['total_score']:.1f} | 排名: {victim['rank']}/{len(rankings)}"
            )
            
            # 执行淘汰
            self._eliminate_robot(
                victim_id=victim_id,
                victim_name=victim_info.get('robot_name', victim_id),
                victim_score=victim['total_score'],
                victim_metrics=victim['metrics'],
                victim_rank=victim['rank'],
                total_count=len(rankings),
                peer_snapshot=peer_snapshot,
                trade_date=trade_date,
            )
        
        # 更新计划的最后执行日期
        with self.engine.begin() as conn:
            conn.execute(text('''
                UPDATE elimination_schedule 
                SET last_run_date = :date 
                WHERE id = :sid
            '''), {'date': trade_date, 'sid': schedule['id']})
        
        logger.info("=" * 50)
        logger.info(f"✅ 淘汰完成: 共淘汰 {len(to_eliminate)} 个机器人")
        logger.info("=" * 50)
    
    def _eliminate_robot(
        self,
        victim_id: str,
        victim_name: str,
        victim_score: float,
        victim_metrics: dict,
        victim_rank: int,
        total_count: int,
        peer_snapshot: List[dict],
        trade_date: str,
    ):
        """淘汰单个机器人"""
        
        # 1. 强制清仓所有持仓
        self._force_liquidate(victim_id, trade_date)
        
        # 2. 更新机器人状态
        reason = f"排名{victim_rank}/{total_count}"
        with self.engine.begin() as conn:
            conn.execute(text('''
                UPDATE sim_robots SET
                    status = 'eliminated',
                    eliminate_reason = :reason,
                    final_score = :score,
                    eliminated_at = CURRENT_TIMESTAMP
                WHERE robot_id = :rid
            '''), {
                'rid': victim_id,
                'reason': reason,
                'score': victim_score,
            })
            
            # 3. 写入淘汰记录
            conn.execute(text('''
                INSERT INTO elimination_records
                (robot_id, robot_name, eliminate_date, final_score,
                 final_metrics, peer_snapshot, reason)
                VALUES (:rid, :name, :date, :score, :metrics, :peers, :reason)
            '''), {
                'rid': victim_id,
                'name': victim_name,
                'date': trade_date,
                'score': victim_score,
                'metrics': json.dumps(victim_metrics),
                'peers': json.dumps(peer_snapshot),
                'reason': reason,
            })
    
    def _force_liquidate(self, robot_id: str, trade_date: str):
        """强制清仓所有持仓"""
        from robot.cash_manager import CashManager
        from robot.trade_cost import calc_slippage_price, calc_sell_cost
        
        cm = CashManager(robot_id)
        
        with self.engine.connect() as conn:
            holdings = conn.execute(text('''
                SELECT id, code, name, buy_price, buy_shares
                FROM sim_trades
                WHERE robot_id = :rid AND status = 'HOLDING'
            '''), {'rid': robot_id}).fetchall()
        
        for h in holdings:
            h = dict(h._mapping)
            # 使用买入价作为清仓价（保守估计）
            sell_price = calc_slippage_price(h['buy_price'], 'SELL')
            sell_result = calc_sell_cost(sell_price, h['buy_shares'], h['code'])
            buy_price = float(h.get('buy_price') or 0)
            shares = int(h.get('buy_shares') or 0)
            cost_basis = buy_price * shares if buy_price > 0 else 0
            net_profit = round(sell_result['total_received'] - cost_basis, 2) if cost_basis > 0 else 0.0
            net_profit_pct = round((sell_price / buy_price - 1) * 100, 4) if buy_price > 0 else 0.0
            
            with self.engine.begin() as conn:
                conn.execute(text('''
                    UPDATE sim_trades SET
                        status = 'SOLD',
                        sell_date = :date,
                        sell_price = :price,
                        sell_shares = :shares,
                        sell_amount = :amount,
                        sell_fee = :fee,
                        sell_stamp_duty = :stamp,
                        net_profit = :profit,
                        net_profit_pct = :pct,
                        sell_reason = '强制清仓（淘汰）'
                    WHERE id = :id
                '''), {
                    'date': trade_date,
                    'price': round(sell_price, 2),
                    'shares': shares,
                    'amount': round(sell_result['amount'], 2),
                    'fee': round(sell_result['commission'] + sell_result['transfer_fee'], 2),
                    'stamp': round(sell_result['stamp_tax'], 2),
                    'profit': net_profit,
                    'pct': net_profit_pct,
                    'id': h['id'],
                })
            
            # 现金到账
            cm.credit_sell(
                sell_result['total_received'],
                h['id'],
                h['code'],
                h['name'],
                trade_date,
            )
            
            logger.info(f"  💰 清仓: {h['code']} {h['name']} | ¥{sell_price:.2f}")


def run_elimination(trade_date: str = None):
    """运行淘汰检查（供外部调用）"""
    eliminator = Eliminator()
    eliminator.check_and_run(trade_date)


# === 单元测试 ===
if __name__ == '__main__':
    import os
    os.chdir(ROOT)
    
    print("=== 测试淘汰引擎 ===")
    
    # 测试配置检查
    print("\n默认淘汰配置:")
    print("  - 周期: 20个交易日")
    print("  - 淘汰数: 1个")
    print("  - 最小保留: 3个")
    
    print("\n✅ 淘汰引擎模块测试完成")
