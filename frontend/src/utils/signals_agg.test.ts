import { describe, it, expect } from 'vitest'
import {
  scoreBuckets, netBuckets, dailyBuckets, summarize,
  type SignalLike,
} from './signals_agg'

const make = (over: Partial<SignalLike> = {}): SignalLike => ({
  ts_code: '000001.SZ',
  trade_date: '2025-01-15',
  reason: '日涨幅偏离值达 7%',
  net_amount: 1e8,
  score: 80,
  ...over,
})

describe('scoreBuckets', () => {
  it('四段计数', () => {
    const rows = [
      make({ score: 10 }), make({ score: 49.9 }),
      make({ score: 60 }),
      make({ score: 70 }), make({ score: 80 }),
      make({ score: 85 }), make({ score: 99.9 }), make({ score: 100 }),
    ]
    const b = scoreBuckets(rows)
    expect(b.map((x) => x.count)).toEqual([2, 1, 2, 3])
  })
  it('忽略 null score', () => {
    const b = scoreBuckets([make({ score: null }), make({ score: 90 })])
    expect(b[3].count).toBe(1)
    expect(b.reduce((a, x) => a + x.count, 0)).toBe(1)
  })
})

describe('netBuckets (单位万元: 1亿=1e4万)', () => {
  it('五档区间', () => {
    const rows = [
      make({ net_amount: -2e4 }),   // -2亿  -> ≤ -1亿
      make({ net_amount: -5e3 }),   // -5千万 -> -1~0
      make({ net_amount: 0 }),      // 0~1
      make({ net_amount: 5e3 }),    // +5千万 -> 0~1
      make({ net_amount: 2e4 }),    // +2亿  -> 1~5
      make({ net_amount: 6e4 }),    // +6亿  -> >5
    ]
    expect(netBuckets(rows).map((x) => x.count)).toEqual([1, 1, 2, 1, 1])
  })
  it('null 视为 0 (0~1亿 桶)', () => {
    expect(netBuckets([make({ net_amount: null })]).map((x) => x.count))
      .toEqual([0, 0, 1, 0, 0])
  })
})

describe('dailyBuckets', () => {
  it('按日期计数并升序', () => {
    const rows = [
      make({ trade_date: '2025-01-15' }),
      make({ trade_date: '2025-01-14' }),
      make({ trade_date: '2025-01-15' }),
      make({ trade_date: '2025-01-16T10:00:00' }),
    ]
    expect(dailyBuckets(rows)).toEqual([
      { date: '2025-01-14', count: 1 },
      { date: '2025-01-15', count: 2 },
      { date: '2025-01-16', count: 1 },
    ])
  })
  it('null 日期跳过', () => {
    expect(dailyBuckets([make({ trade_date: null })])).toEqual([])
  })
})

describe('summarize', () => {
  it('空数组', () => {
    const s = summarize([])
    expect(s.count).toBe(0)
    expect(s.avgScore).toBeNull()
    expect(s.netSum).toBe(0)
    expect(s.netAvg).toBeNull()
    expect(s.topReasons).toEqual([])
    expect(s.uniqStocks).toBe(0)
  })
  it('均值 / topReasons / uniq', () => {
    const rows = [
      make({ ts_code: 'A', score: 60, net_amount: 1e8, reason: 'R1' }),
      make({ ts_code: 'B', score: 80, net_amount: 2e8, reason: 'R1' }),
      make({ ts_code: 'A', score: null, net_amount: -1e8, reason: 'R2' }),
    ]
    const s = summarize(rows, 3)
    expect(s.count).toBe(3)
    expect(s.avgScore).toBe(70)
    expect(s.netSum).toBe(2e8)
    expect(s.netAvg).toBeCloseTo(2e8 / 3)
    expect(s.topReasons[0]).toEqual({ reason: 'R1', count: 2 })
    expect(s.uniqStocks).toBe(2)
  })
  it('空 reason -> (未知)', () => {
    const s = summarize([make({ reason: null }), make({ reason: '' })])
    expect(s.topReasons[0].reason).toBe('(未知)')
    expect(s.topReasons[0].count).toBe(2)
  })
})
