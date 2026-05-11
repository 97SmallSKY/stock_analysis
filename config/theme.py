"""全局配色与图表样式常量，所有页面统一引用此处。"""


class Color:
    RED   = "#E84040"        # 涨/买入（A股：红涨）
    GREEN = "#33CC66"        # 跌/卖出（A股：绿跌）
    GOLD  = "#FFA500"        # 橙色 / MA5 / 警告
    BLUE  = "#4488FF"        # 蓝色 / MA20 / 信息
    YELLOW = "#FFCC00"       # 正常期 / 中性
    GRAY  = "#888888"        # 次要文字
    WHITE = "#e0e0e0"        # 主要文字
    BG    = "rgba(0,0,0,0)"  # 透明背景（Plotly 暗色主题用）
