import { describe, it, expect } from 'vitest'
import { truncateTradesBeforeDate, type TradePoint } from './backtest_truncate'

const SAMPLE: TradePoint[] = [
  { date: '2026-01-03', side: 'BUY', price: 10 },
  { date: '2026-01-05', side: 'BUY', price: 11 },
  { date: '2026-01-10', side: 'SELL', price: 12 },
]

describe('truncateTradesBeforeDate · AC (e) 无未来函数验收', () => {
  it('currentDate=undefined → 空视图(视角未定)', () => {
    expect(truncateTradesBeforeDate(SAMPLE, undefined)).toEqual([])
  })

  it('currentDate=空串 → 空视图', () => {
    expect(truncateTradesBeforeDate(SAMPLE, '')).toEqual([])
  })

  it('points 空 → 空视图', () => {
    expect(truncateTradesBeforeDate([], '2026-01-10')).toEqual([])
  })

  it('t=2026-01-04:只看得到 01-03,看不到 01-05/01-10', () => {
    const out = truncateTradesBeforeDate(SAMPLE, '2026-01-04')
    expect(out).toHaveLength(1)
    expect(out[0].date).toBe('2026-01-03')
  })

  it('t 等于某买卖点当天 → 当天那条被排除(严格 <)', () => {
    const out = truncateTradesBeforeDate(SAMPLE, '2026-01-05')
    // 01-05 当天那条排除;01-03 保留
    expect(out.map((p) => p.date)).toEqual(['2026-01-03'])
  })

  it('t 晚于所有点 → 全部返回', () => {
    const out = truncateTradesBeforeDate(SAMPLE, '2026-12-31')
    expect(out).toHaveLength(3)
  })

  it('t 早于所有点 → 空', () => {
    const out = truncateTradesBeforeDate(SAMPLE, '2020-01-01')
    expect(out).toHaveLength(0)
  })
})
