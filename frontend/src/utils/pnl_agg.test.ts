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

describe('aggregateByReason - P16.5 TN 维度', () => {
  it('avg_t1 / avg_t5 / t5_win_rate 计算', () => {
    const rows = [
      make({ reason: 'A', t1_pnl_pct: 0.02, t5_pnl_pct: 0.05 }),
      make({ reason: 'A', t1_pnl_pct: -0.01, t5_pnl_pct: -0.02 }),
      make({ reason: 'A', t1_pnl_pct: 0.03, t5_pnl_pct: 0.08 }),
    ]
    const out = aggregateByReason(rows, 10)
    const A = out.find((x) => x.reason === 'A')!
    expect(A.avg_t1).toBeCloseTo((0.02 - 0.01 + 0.03) / 3, 6)
    expect(A.avg_t5).toBeCloseTo((0.05 - 0.02 + 0.08) / 3, 6)
    expect(A.t5_win_rate).toBeCloseTo(2 / 3, 6)
  })

  it('无 TN 数据 → avg_t1/avg_t5/t5_win_rate=null', () => {
    const rows = [make({ reason: 'X' }), make({ reason: 'X' })]
    const out = aggregateByReason(rows)
    const X = out.find((x) => x.reason === 'X')!
    expect(X.avg_t1).toBeNull()
    expect(X.avg_t5).toBeNull()
    expect(X.t5_win_rate).toBeNull()
  })

  it('忽略 NaN/null 的 TN 值', () => {
    const rows = [
      make({ reason: 'B', t1_pnl_pct: 0.04, t5_pnl_pct: null }),
      make({ reason: 'B', t1_pnl_pct: NaN, t5_pnl_pct: 0.10 }),
      make({ reason: 'B', t1_pnl_pct: 0.02, t5_pnl_pct: 0.05 }),
    ]
    const out = aggregateByReason(rows)
    const B = out.find((x) => x.reason === 'B')!
    expect(B.avg_t1).toBeCloseTo((0.04 + 0.02) / 2, 6)
    expect(B.avg_t5).toBeCloseTo((0.10 + 0.05) / 2, 6)
    expect(B.t5_win_rate).toBe(1)
  })
})
