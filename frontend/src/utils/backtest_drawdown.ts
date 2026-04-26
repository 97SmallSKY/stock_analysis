// STO-90 / STO-80c: 从 nav_series 推导回撤曲线 (peak - nav) / peak
//
// 纯函数,与 Vue 解耦,便于 vitest 覆盖。

export interface NavLike {
  date: string
  nav: number
}

export interface DrawdownPoint {
  date: string
  drawdown: number   // 负数或 0,eg -0.12 表示回撤 12%
}

/**
 * 给定 nav_series,返回每日回撤(相对历史最高 nav)。
 *
 * 性质:单调递增时全 0;触及新高时该点回撤 0;跌破新高时回撤为负。
 */
export function computeDrawdown(series: NavLike[]): DrawdownPoint[] {
  if (!series || series.length === 0) return []
  const out: DrawdownPoint[] = []
  let peak = series[0].nav
  for (const p of series) {
    if (p.nav > peak) peak = p.nav
    // 保护 peak=0 防除零,正常 nav 起点 1.0
    const dd = peak > 0 ? (p.nav - peak) / peak : 0
    out.push({ date: p.date, drawdown: Math.min(0, dd) })
  }
  return out
}
