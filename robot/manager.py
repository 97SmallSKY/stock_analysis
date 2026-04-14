"""
机器人 CRUD 管理

职责：创建、启动、暂停、结束、继承、淘汰
"""
import sys
import uuid
import logging
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from utils.storage import storage
from sqlalchemy import text
from .cash_manager import CashManager
from strategy.loader import load_strategy

logger = logging.getLogger(__name__)


class RobotManager:
    
    def __init__(self):
        self.engine = storage._get_engine()
    
    def create_robot(self, config: dict) -> str:
        """创建新机器人"""
        robot_id = f"robot_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        
        # 验证策略存在
        select_strategy = config.get('select_strategy', 'lianban_multi_factor')
        try:
            load_strategy(select_strategy)
        except Exception as e:
            logger.error(f"策略加载失败: {e}")
            raise
        
        with self.engine.begin() as conn:
            conn.execute(text('''
                INSERT INTO sim_robots 
                (robot_id, robot_name, status, stock_source,
                 select_strategy, execute_strategy,
                 initial_capital, position_ratio, max_positions,
                 max_holding_days, stop_loss_pct, stop_profit_pct,
                 enable_batch_buy, strategy_params, cost_config)
                VALUES 
                (:rid, :name, 'draft', :source,
                 :sel_strategy, :exe_strategy,
                 :capital, :ratio, :max_pos,
                 :max_days, :stop_loss, :stop_profit,
                 :batch, :params::jsonb, :cost::jsonb)
            '''), {
                'rid': robot_id,
                'name': config.get('robot_name', f'机器人-{robot_id[:8]}'),
                'source': config.get('stock_source', 'longhubang'),
                'sel_strategy': select_strategy,
                'exe_strategy': config.get('execute_strategy', select_strategy),
                'capital': config.get('initial_capital', 100000),
                'ratio': config.get('position_ratio', 0.1),
                'max_pos': config.get('max_positions', 3),
                'max_days': config.get('max_holding_days', 7),
                'stop_loss': config.get('stop_loss_pct', -0.5),
                'stop_profit': config.get('stop_profit_pct'),
                'batch': config.get('enable_batch_buy', False),
                'params': str(config.get('strategy_params', {})).replace("'", '"'),
                'cost': str(config.get('cost_config', {})).replace("'", '"'),
            })
        
        logger.info(f"🤖 机器人已创建: {robot_id}")
        return robot_id
    
    def start_robot(self, robot_id: str, trade_date: str = None):
        """启动机器人（draft → running）"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        
        with self.engine.begin() as conn:
            robot = self._get_robot(conn, robot_id)
            
            if robot['status'] not in ('draft', 'paused'):
                raise ValueError(f"机器人状态为 {robot['status']}，无法启动")
            
            conn.execute(text('''
                UPDATE sim_robots 
                SET status = 'running', started_at = COALESCE(started_at, NOW())
                WHERE robot_id = :rid
            '''), {'rid': robot_id})
        
        # 初始化现金
        cm = CashManager(robot_id)
        if cm.get_balance() == 0:
            cm.init_capital(robot['initial_capital'], trade_date)
        
        logger.info(f"🟢 机器人已启动: {robot_id}")
    
    def pause_robot(self, robot_id: str):
        """暂停机器人"""
        with self.engine.begin() as conn:
            conn.execute(text('''
                UPDATE sim_robots SET status = 'paused' WHERE robot_id = :rid
            '''), {'rid': robot_id})
        logger.info(f"⏸️ 机器人已暂停: {robot_id}")
    
    def resume_robot(self, robot_id: str):
        """恢复机器人"""
        self.start_robot(robot_id)
    
    def end_robot(self, robot_id: str, trade_date: str = None):
        """结束机器人（强制清仓）"""
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        
        with self.engine.begin() as conn:
            # 获取持仓
            positions = conn.execute(text('''
                SELECT id, code, name, buy_shares 
                FROM sim_trades 
                WHERE robot_id = :rid AND status = 'HOLDING'
            '''), {'rid': robot_id}).fetchall()
            
            if positions:
                logger.warning(f"⚠️ 机器人 {robot_id} 还有 {len(positions)} 个持仓需要清仓")
                # TODO: 实现清仓逻辑（需要实时价格）
            
            conn.execute(text('''
                UPDATE sim_robots 
                SET status = 'ended', ended_at = NOW()
                WHERE robot_id = :rid
            '''), {'rid': robot_id})
        
        logger.info(f"🔴 机器人已结束: {robot_id}")
    
    def fork_robot(self, source_robot_id: str, new_config: dict) -> str:
        """创建继承机器人"""
        with self.engine.begin() as conn:
            source = self._get_robot(conn, source_robot_id)
        
        # 继承配置
        inherited_config = {
            'robot_name': new_config.get('robot_name', f"{source['robot_name']}（继承）"),
            'stock_source': source.get('stock_source', 'longhubang'),
            'select_strategy': source.get('select_strategy', 'lianban_multi_factor'),
            'execute_strategy': source.get('execute_strategy', 'lianban_multi_factor'),
            'initial_capital': new_config.get('initial_capital', source.get('initial_capital', 100000)),
            'position_ratio': source.get('position_ratio', 0.1),
            'max_positions': source.get('max_positions', 3),
            'max_holding_days': source.get('max_holding_days', 7),
            'stop_loss_pct': source.get('stop_loss_pct', -0.5),
            'stop_profit_pct': source.get('stop_profit_pct'),
            'enable_batch_buy': source.get('enable_batch_buy', False),
            'strategy_params': source.get('strategy_params', {}),
            **new_config
        }
        
        new_robot_id = self.create_robot(inherited_config)
        
        # 标记继承关系
        with self.engine.begin() as conn:
            conn.execute(text('''
                UPDATE sim_robots SET parent_robot_id = :parent WHERE robot_id = :rid
            '''), {'parent': source_robot_id, 'rid': new_robot_id})
        
        logger.info(f"🔀 机器人继承: {source_robot_id} → {new_robot_id}")
        return new_robot_id
    
    def list_robots(self, status: str = None) -> list[dict]:
        """列出机器人"""
        with self.engine.connect() as conn:
            if status:
                rows = conn.execute(text('''
                    SELECT r.*,
                           (SELECT COUNT(*) FROM sim_trades WHERE robot_id = r.robot_id AND status = 'HOLDING') as holding_count,
                           (SELECT balance_after FROM cash_ledger WHERE robot_id = r.robot_id ORDER BY id DESC LIMIT 1) as cash_balance
                    FROM sim_robots r WHERE r.status = :status
                    ORDER BY r.created_at DESC
                '''), {'status': status}).fetchall()
            else:
                rows = conn.execute(text('''
                    SELECT r.*,
                           (SELECT COUNT(*) FROM sim_trades WHERE robot_id = r.robot_id AND status = 'HOLDING') as holding_count,
                           (SELECT balance_after FROM cash_ledger WHERE robot_id = r.robot_id ORDER BY id DESC LIMIT 1) as cash_balance
                    FROM sim_robots r ORDER BY r.created_at DESC
                ''')).fetchall()
        
        return [dict(r._mapping) for r in rows]
    
    def get_robot(self, robot_id: str) -> dict:
        """获取单个机器人信息"""
        with self.engine.connect() as conn:
            return self._get_robot(conn, robot_id)
    
    def _get_robot(self, conn, robot_id: str) -> dict:
        row = conn.execute(text(
            'SELECT * FROM sim_robots WHERE robot_id = :rid'
        ), {'rid': robot_id}).fetchone()
        
        if not row:
            raise ValueError(f"机器人 {robot_id} 不存在")
        
        return dict(row._mapping)


# === 单元测试 ===
if __name__ == '__main__':
    import os
    os.chdir(ROOT)
    
    print("=== 测试 RobotManager ===")
    
    rm = RobotManager()
    
    # 列出所有机器人
    robots = rm.list_robots()
    print(f"已有机器人: {len(robots)}")
    for r in robots:
        print(f"  - {r['robot_id']}: {r['robot_name']} ({r['status']})")
    
    # 测试获取默认机器人
    default_robot = rm.get_robot('default')
    print(f"\n默认机器人: {default_robot['robot_name']}")
