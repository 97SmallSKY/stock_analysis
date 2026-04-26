import { describe, it, expect } from 'vitest'
import { computeDrawdown, type NavLike } from './backtest_drawdown'

describe('computeDrawdown', () => {
  it('空序列返回空', () => {
    expect(computeDrawdown([])).toEqual([])
  })

  it('单调上涨:所有回撤 = 0', () => {
    const s: NavLike[] = [
      { date: '2026-01-01', nav: 1.0 },
      { date: '2026-01-02', nav: 1.05 },
      { date: '2026-01-03', nav: 1.1 },
    ]
    const dd = computeDrawdown(s)
    expect(dd.map((p) => p.drawdown)).toEqual([0, 0, 0])
  })

  it('峰后回撤:dd = (nav - peak) / peak,负数', () => {
    const s: NavLike[] = [
      { date: '2026-01-01', nav: 1.0 },
      { date: '2026-01-02', nav: 1.2 },
      { date: '2026-01-03', nav: 1.0 },
    ]
    const dd = computeDrawdown(s)
    // peak=1.2, 第 3 点 = (1.0-1.2)/1.2 ≈ -0.1667
    expect(dd[0].drawdown).toBe(0)
    expect(dd[1].drawdown).toBe(0)
    expect(dd[2].drawdown).toBeCloseTo(-1 / 6, 4)
  })

  it('触及新高后该点回撤归 0', () => {
    const s: NavLike[] = [
      { date: '2026-01-01', nav: 1.0 },
      { date: '2026-01-02', nav: 1.2 },
      { date: '2026-01-03', nav: 0.9 },
      { date: '2026-01-04', nav: 1.3 },     // 新高
    ]
    const dd = computeDrawdown(s)
    expect(dd[2].drawdown).toBeLessThan(0)
    expect(dd[3].drawdown).toBe(0)
  })

  it('极端:nav 触底跌去 50%', () => {
    const s: NavLike[] = [
      { date: 'a', nav: 2.0 },
      { date: 'b', nav: 1.0 },
    ]
    const dd = computeDrawdown(s)
    expect(dd[1].drawdown).toBe(-0.5)
  })
})
