"""
全局配置文件
- 本地开发：使用 Excel 存储数据
- 远程部署：切换为 PostgreSQL 数据库
"""
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# 存储模式配置
# ============================================================
# 'excel' = 本地开发模式（默认）
# 'postgres' = 远程部署模式
STORAGE_MODE = os.getenv("STORAGE_MODE", "excel")

# ============================================================
# Excel 配置
# ============================================================
EXCEL_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
EXCEL_FILES = {
    "lhb_detail":       os.path.join(EXCEL_DATA_DIR, "lhb_detail.xlsx"),       # 龙虎榜明细
    "lhb_jigou":        os.path.join(EXCEL_DATA_DIR, "lhb_jigou.xlsx"),         # 机构汇总
    "lhb_ggtj":         os.path.join(EXCEL_DATA_DIR, "lhb_ggtj.xlsx"),          # 个股上榜统计
    "lhb_seat_detail":  os.path.join(EXCEL_DATA_DIR, "lhb_seat_detail.xlsx"),   # 当日席位明细（新）
    "stock_kline":      os.path.join(EXCEL_DATA_DIR, "stock_kline.xlsx"),       # 个股K线
    "backtest_results": os.path.join(EXCEL_DATA_DIR, "backtest_results.xlsx"),  # 回测结果
    "selected_stocks":  os.path.join(EXCEL_DATA_DIR, "selected_stocks.xlsx"),   # 每日选股
    "market_sentiment": os.path.join(EXCEL_DATA_DIR, "market_sentiment.xlsx"),  # 市场情绪
}

# ============================================================
# PostgreSQL 配置
# ============================================================
# 数据库拓扑：
#   videofetcher-db Docker 容器（共用）
#     └── videofetcher 库  ← videofetcher 服务（保持独立）
#     └── stock_analysis 库 ← 本项目专属（独立隔离）
#
# 切换方式：修改 .env 中 PG_HOST 即可无感知切换本地↔云上
from urllib.parse import quote_plus as _qp

PG_CONFIG = {
    "host":     os.getenv("PG_HOST",     "localhost"),
    "port":     int(os.getenv("PG_PORT", 5432)),
    "database": os.getenv("PG_DATABASE", "stock_analysis"),
    "user":     os.getenv("PG_USER",     "stock_user"),
    "password": os.getenv("PG_PASSWORD", ""),
}
# 密码含特殊字符（! @ # 等）时需 URL 编码，否则 SQLAlchemy 解析报错
PG_URL = (
    f"postgresql://{_qp(PG_CONFIG['user'])}:{_qp(PG_CONFIG['password'])}"
    f"@{PG_CONFIG['host']}:{PG_CONFIG['port']}/{PG_CONFIG['database']}"
)

# 表名前缀：独立库不需要前缀，置空即可
# 若未来复用其他库，可设为 'sa_' 等
PG_TABLE_PREFIX = os.getenv("PG_TABLE_PREFIX", "")

# ============================================================
# 数据时间范围
# ============================================================
# 最近半年数据
HALF_YEAR_START = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
DATA_END_DATE = datetime.now().strftime("%Y%m%d")

# ============================================================
# 策略参数
# ============================================================
STRATEGY_CONFIG = {
    # 净买入阈值（5000万）
    "min_net_buy": 50_000_000,
    # 最低技术面评分
    "min_tech_score": 50,
    # 最高20日涨幅
    "max_gain_20d": 0.30,
    # 每日最多选股数
    "top_n": 3,
    # 持有天数
    "hold_days": 1,
    # 止损比例
    "stop_loss": -0.05,
    # 止盈比例
    "stop_profit": 0.08,
    # 最大持有天数
    "max_hold_days": 3,
    # 最小涨停数（市场情绪）
    "min_limit_up": 30,
    # 最大跌停数（市场情绪）
    "max_limit_down": 20,
}

# ============================================================
# 知名游资席位
# ============================================================
FAMOUS_TRADERS = {
    "中国银河证券绍兴营业部": "赵老哥",
    "华泰证券深圳益田路荣超商务中心": "深圳帮",
    "东方财富证券拉萨团结路第二": "拉萨天团",
    "东方财富证券拉萨东环路第二": "拉萨天团",
    "中信证券上海溧阳路": "溧阳路",
    "华鑫证券上海宛平南路": "方新侠",
    "国泰君安证券成都北一环路": "成都帮",
    "华泰证券南京虎踞南路": "南京帮",
    "中信建投证券杭州市心南路": "杭州帮",
    "申万宏源西部证券上海闵行区东川路": "顶级游资",
    "财通证券绍兴人民中路": "财通帮",
}

# ============================================================
# 通知配置（可选）
# ============================================================
NOTIFY_CONFIG = {
    "email_from": os.getenv("EMAIL_FROM", ""),
    "email_password": os.getenv("EMAIL_PASSWORD", ""),
    "email_to": os.getenv("EMAIL_TO", ""),
    "dingtalk_webhook": os.getenv("DINGTALK_WEBHOOK", ""),
    "server_chan_key": os.getenv("SERVER_CHAN_KEY", ""),
}

# ============================================================
# Streamlit 配置
# ============================================================
APP_TITLE = "龙虎榜交易策略分析系统"
APP_ICON = "📈"
APP_VERSION = "1.0.0"
