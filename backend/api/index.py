"""STO-82: 四大指数实时行情 API。

直接调 akshare.stock_zh_index_spot_em() 取实时快照,本 API 不落盘(每次请求都查)。
bootstrap() 清代理保证 jd 服务器上能连国内源;10s 硬超时防挂死。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.core.startup import bootstrap

bootstrap()

logger = logging.getLogger(__name__)
router = APIRouter()

# 目标四大指数:AkShare 用代码字段识别(不同接口字段名不同,这里按 stock_zh_index_spot_em 的「代码」列)
_TARGET: dict[str, dict[str, Any]] = {
    "sh000001": {"code": "sh000001", "name": "上证指数"},
    "sz399001": {"code": "sz399001", "name": "深证成指"},
    "sz399006": {"code": "sz399006", "name": "创业板指"},
    "sh000300": {"code": "sh000300", "name": "沪深300"},
}


def _run_with_timeout(fn, timeout: float):
    """在子线程中跑 fn,超过 timeout 秒抛 TimeoutError。akshare 不支持 timeout 参数,只能硬切。"""
    result: dict[str, Any] = {}

    def _target():
        try:
            result["value"] = fn()
        except Exception as e:  # pragma: no cover - 走到这里说明 akshare 抛了
            result["error"] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"akshare 调用超过 {timeout}s")
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _fetch_index_spot() -> dict:
    """返回 {target_name: {name,latest,change,pct}}。

    失败时不抛异常,返回 partial(部分字段 None)。前端据此显示「-」。
    """
    import akshare as ak

    df = _run_with_timeout(ak.stock_zh_index_spot_em, timeout=10.0)
    if df is None or df.empty:
        return {"items": [], "error": "empty"}

    # AkShare 返回的列名(中文):代码 / 名称 / 最新价 / 涨跌额 / 涨跌幅
    # 把中文列名统一成英文,挑出四大
    rename_map = {
        "代码": "code",
        "名称": "name",
        "最新价": "latest",
        "涨跌额": "change",
        "涨跌幅": "pct",
    }
    df = df.rename(columns=rename_map)
    items = []
    for _, row in df.iterrows():
        code = str(row.get("code") or "").lower()
        if code in _TARGET:
            try:
                items.append({
                    "code": code,
                    "name": _TARGET[code]["name"],
                    "latest": float(row.get("latest") or 0),
                    "change": float(row.get("change") or 0),
                    "pct": float(row.get("pct") or 0),
                })
            except (TypeError, ValueError):
                continue
    # 保持顺序:上证/深证/创业板/沪深300
    order = ["sh000001", "sz399001", "sz399006", "sh000300"]
    items.sort(key=lambda x: order.index(x["code"]) if x["code"] in order else 99)
    return {"items": items}


@router.get("/snapshot")
def get_index_snapshot():
    """GET /api/index/snapshot —— 四大指数实时行情快照。

    返回体:``{"items": [{code,name,latest,change,pct}, ...]}``;
    网络失败 / akshare 超时 → 502,前端显示「数据暂不可用」。
    """
    try:
        return _fetch_index_spot()
    except TimeoutError as e:
        logger.warning("[api/index] akshare timeout: %s", e)
        raise HTTPException(502, "指数数据源超时")
    except Exception as e:
        logger.exception("[api/index] fetch failed")
        raise HTTPException(502, f"指数数据源错误: {e}")
