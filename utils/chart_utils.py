"""Plotly 图表公共配置（审计文档 Phase 1），消除各页面 26+ 处重复的暗色主题定义。"""
from config.theme import Color

# 仅含各图共有键；height/margin/xaxis/yaxis 由调用方按需传入 overrides 避免相互覆盖
_PLOTLY_DARK = dict(
    plot_bgcolor=Color.BG,
    paper_bgcolor=Color.BG,
    font=dict(color=Color.WHITE),
)


def plotly_dark(**overrides) -> dict:
    """返回 Plotly 暗色主题 layout dict。
    overrides 中的 height/margin/xaxis/yaxis/title 等会合并进返回值。
    """
    return {**_PLOTLY_DARK, **overrides}
