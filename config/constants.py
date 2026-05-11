"""魔法数字集中定义（缓存超时、时间窗口等）。"""


class TTL:
    SHORT   = 60    # 实时行情缓存
    MEDIUM  = 120   # 盘中数据
    DEFAULT = 300   # 通用（5 分钟）
    LONG    = 600   # 历史/低频数据


class Window:
    RECENT    = 90   # 近 3 个月
    HALF_YEAR = 180  # 近半年
    ANNUAL    = 365  # 近 1 年
