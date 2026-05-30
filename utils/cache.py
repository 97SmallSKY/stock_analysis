"""统一 session_state 缓存工具（消除各页面 10 处重复定义）。"""
import time
import streamlit as st
from typing import Any

_NS = "_ss_cache"  # 统一命名空间，避免与其他 session_state 键冲突


def ss_get(key: str, ttl: int = 300) -> Any:
    """取缓存值；超过 ttl 秒或不存在则返回 None。"""
    cache = st.session_state.get(_NS, {})
    entry = cache.get(key)
    if entry is None or (time.time() - entry["ts"]) > ttl:
        return None
    return entry["val"]


def ss_set(key: str, val: Any) -> None:
    """写入缓存，附带当前时间戳。"""
    if _NS not in st.session_state:
        st.session_state[_NS] = {}
    st.session_state[_NS][key] = {"ts": time.time(), "val": val}
