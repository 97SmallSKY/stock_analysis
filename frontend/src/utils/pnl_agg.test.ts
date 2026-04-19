import { describe, it, expect } from 'vitest'
import {
  aggregateByReason, aggregateByStock, summarize,
  PNL_COLORS,
  type LhbRowLike,
} from './pnl_agg'

const make = (over: Partial<LhbRowLike> = {}): LhbRowLike => ({
  ts_code: '000001.SZ',
  name: '平安银行',
  reason: '日涨幅偏离值达7%',
  buy_amount: 1e8,
  sell_amount: 5e7,
  net_amount: 5e7,
  score: 80,
  ...over,
})

describe('aggregateByReason', () => {
  it('按原因合计 net/cnt/avg_score,按 |net| 排序取 topN', () => {
    const rows = [
      make({ reason: 'A', net_amount: 1e8, score: 80 }),
      make({ reason: 'A', net_amount: -5e7, score: 90 }),
      make({ reason: 'B', net_amount: 3e8, score: 70 }),
      make({ reason: 'C', net_amount: 1e7, score: null }),
    ]
    const out = aggregateByReason(rows, 10)
    expect(out[0].reason).toBe('B')
    expect(out[0].net_sum).toBe(3e8)
    const A = out.find((x) => x.reason === 'A')!
    expect(A.count).toBe(2)
    expect(A.net_sum).toBe(5e7)
    expect(A.avg_score).toBe(85)
    expect(A.avg_net).toBe(2.5e7)
    const C = out.find((x) => x.reason === 'C')!
    expect(C.avg_score).toBe(null)
  })

  it('空 reason 归为(未知)', () => {
    const out = aggregateByReason([make({ reason: null }), make({ reason: '   ' })])
    expect(out.length).toBe(1)
    expect(out[0].reason).toBe('(未知)')
    expect(out[0].count).toBe(2)
  })

  it('topN 截断', () => {
    const rows = Array.from({ length: 15 }, (_, i) =>
      make({ reason: `R${i}`, net_amount: (i + 1) * 1e6 }),
    )
    expect(aggregateByReason(rows, 5).length).toBe(5)
  })
})

describe('aggregateByStock', () => {
  it('按 ts_code 合计', () => {
    const rows = [
      make({ ts_code: '600000.SH', name: 'A', buy_amount: 2e8, sell_amount: 1e8, net_amount: 1e8, score: 70 }),
      make({ ts_code: '600000.SH', name: 'A', buy_amount: 1e8, sell_amount: 0, net_amount: 1e8, score: 90 }),
      make({ ts_code: '000001.SZ', name: 'B', buy_amount: 5e7, sell_amount: 1e8, net_amount: -5e7, score: null }),
    ]
    const out = aggregateByStock(rows)
    const A = out.find((x) => x.ts_code === '600000.SH')!
    expect(A.count).toBe(2)
    expect(A.buy_sum).toBe(3e8)
    expect(A.net_sum).toBe(2e8)
    expect(A.avg_score).toBe(80)
    expect(A.avg_net).toBe(1e8)
    const B = out.find((x) => x.ts_code === '000001.SZ')!
    expect(B.avg_score).toBe(null)
  })
})

describe('summarize', () => {
  it('累加并给平均分', () => {
    const rows = [
      make({ buy_amount: 1e8, sell_amount: 5e7, net_amount: 5e7, score: 60 }),
      make({ buy_amount: 2e8, sell_amount: 1e8, net_amount: 1e8, score: 80 }),
      make({ score: null }),
    ]
    const s = summarize(rows)
    expect(s.count).toBe(3)
    expect(s.buy_sum).toBe(4e8)
    expect(s.avg_score).toBe(70)
  })
  it('空数组', () => {
    const s = summarize([])
    expect(s.count).toBe(0)
    expect(s.avg_score).toBe(null)
  })
})

describe('PNL_COLORS', () => {
  it('三系列固定色', () => {
    expect(PNL_COLORS.netPos).toBe('#d4380d')
    expect(PNL_COLORS.count).toBe('#fa8c16')
    expect(PNL_COLORS.score).toBe('#722ed1')
  })
})
