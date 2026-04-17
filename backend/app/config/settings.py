"""
应用配置模块
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import quote_plus

# 加载环境变量
env_path = Path(__file__).parent.parent.parent.parent / '.env'
load_dotenv(env_path)


class Settings:
    """应用配置"""
    
    # 数据库配置
    PG_HOST = os.getenv('PG_HOST', 'localhost')
    PG_PORT = os.getenv('PG_PORT', '5432')
    PG_DATABASE = os.getenv('PG_DATABASE', 'stock_analysis')
    PG_USER = os.getenv('PG_USER', 'postgres')
    PG_PASSWORD = os.getenv('PG_PASSWORD', '')
    
    # 构建数据库 URL
    @property
    def DATABASE_URL(self) -> str:
        """异步数据库连接 URL（asyncpg）"""
        password = quote_plus(self.PG_PASSWORD)
        return f"postgresql+asyncpg://{self.PG_USER}:{password}@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DATABASE}"
    
    @property
    def SYNC_DATABASE_URL(self) -> str:
        """同步数据库连接 URL（psycopg2）"""
        password = quote_plus(self.PG_PASSWORD)
        return f"postgresql+psycopg2://{self.PG_USER}:{password}@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DATABASE}"
    
    # 调试模式
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'


# 全局配置实例
settings = Settings()
