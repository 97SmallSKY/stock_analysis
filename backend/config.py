"""全局配置加载（从 .env / 环境变量）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class DBSettings:
    host: str = field(default_factory=lambda: _get("PG_HOST", "localhost"))
    port: int = field(default_factory=lambda: _int("POSTGRES_PORT", 5432))
    database: str = field(default_factory=lambda: _get("POSTGRES_DB", "lhb_quant"))
    user: str = field(default_factory=lambda: _get("POSTGRES_USER", "lhb_admin"))
    password: str = field(default_factory=lambda: _get("POSTGRES_PASSWORD", ""))

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


@dataclass
class DataSourceSettings:
    tushare_token: str = field(default_factory=lambda: _get("TUSHARE_TOKEN"))
    tushare_points: int = field(default_factory=lambda: _int("TUSHARE_POINTS", 120))


@dataclass
class Settings:
    db: DBSettings = field(default_factory=DBSettings)
    ds: DataSourceSettings = field(default_factory=DataSourceSettings)
    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
