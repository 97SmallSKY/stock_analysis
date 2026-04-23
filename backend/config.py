"""全局配置加载（从 .env / 环境变量）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

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


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> List[str]:
    """逗号分隔的字符串列表,去空白、去空项。"""
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


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
class AuthSettings:
    """API 鉴权 + 限流配置 (STO-6)。

    - ``enabled=False`` 时 ``require_api_key`` 放行,限流仍生效(可独立控制)。
    - ``api_keys`` 为空则等同于 ``enabled=False``(避免误部署锁死)。
    - 限流字符串格式遵循 ``limits`` 库语法: "60/minute", "1000/hour" 等。
    """
    enabled: bool = field(default_factory=lambda: _bool("AUTH_ENABLED", False))
    api_keys: List[str] = field(default_factory=lambda: _list("API_KEYS"))
    rate_limit_enabled: bool = field(
        default_factory=lambda: _bool("RATE_LIMIT_ENABLED", True)
    )
    rate_limit_default: str = field(
        default_factory=lambda: _get("RATE_LIMIT_DEFAULT", "60/minute")
    )
    rate_limit_write: str = field(
        default_factory=lambda: _get("RATE_LIMIT_WRITE", "30/minute")
    )

    @property
    def effective_enabled(self) -> bool:
        """只有 enabled=True 且配置了至少一个 key 才真正生效。"""
        return self.enabled and len(self.api_keys) > 0


@dataclass
class AllowedOriginsSettings:
    """CORS allowlist (STO-58)。

    空列表 = 同源场景无需 CORS(jd 生产:前端 SPA 和 API 都在 :8000)。
    非空 = 只允许列出源跨域;未列源 OPTIONS 响应不带 Access-Control-Allow-Origin。
    """
    origins: List[str] = field(default_factory=lambda: _list("ALLOWED_ORIGINS"))


@dataclass
class Settings:
    db: DBSettings = field(default_factory=DBSettings)
    ds: DataSourceSettings = field(default_factory=DataSourceSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    cors: AllowedOriginsSettings = field(default_factory=AllowedOriginsSettings)
    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
