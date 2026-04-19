import { describe, it, expect } from 'vitest'
import { tnSummarize, type TnRowLike } from './tn_summary'

describe('tnSummarize', () => {
  it('空数组 → 全 null', () => {
    const s = tnSummarize([])
    expect(s.t1Cnt).toBe(0)
    expect(s.t1Avg).toBeNull()
    expect(s.t1WinRate).toBeNull()
    expect(s.t5Avg).toBeNull()
  })

  it('只统计数字型 pnl, 忽略 null/undefined/NaN', () => {
    const rows: TnRowLike[] = [
      { t1_pnl_pct: 0.05, t5_pnl_pct: 0.10 },
      { t1_pnl_pct: -0.02, t5_pnl_pct: null },
      { t1_pnl_pct: null, t5_pnl_pct: 0.03 },
      { t1_pnl_pct: NaN, t5_pnl_pct: undefined as unknown as number },
    ]
    const s = tnSummarize(rows)
    expect(s.t1Cnt).toBe(2)
    expect(s.t1Avg).toBeCloseTo((0.05 - 0.02) / 2, 6)
    expect(s.t1WinRate).toBeCloseTo(0.5, 6)
    expect(s.t5Cnt).toBe(2)
    expect(s.t5Avg).toBeCloseTo((0.10 + 0.03) / 2, 6)
    expect(s.t5WinRate).toBe(1)
  })

  it('limit_up 计数', () => {
    const s = tnSummarize([
      { t1_pnl_pct: 0.1, t1_limit_up: true },
      { t1_pnl_pct: 0.1, t1_limit_up: true },
      { t1_pnl_pct: 0.1, t1_limit_up: false },
      { t1_pnl_pct: 0.1, t1_limit_up: null },
    ])
    expect(s.t1LimitUpCnt).toBe(2)
  })

  it('全部亏损时胜率=0, 平均<0', () => {
    const s = tnSummarize([
      { t1_pnl_pct: -0.01 },
      { t1_pnl_pct: -0.03 },
    ])
    expect(s.t1WinRate).toBe(0)
    expect(s.t1Avg).toBeCloseTo(-0.02, 6)
  })

  it('t5_high_pct 平均', () => {
    const s = tnSummarize([
      { t5_high_pct: 0.08 },
      { t5_high_pct: 0.12 },
      { t5_high_pct: null },
    ])
    expect(s.t5HighAvg).toBeCloseTo(0.10, 6)
  })
})
