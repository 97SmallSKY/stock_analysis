import { describe, it, expect } from 'vitest'
import { summarizeHistory, type HistoryRow } from './backtest_history'

const r = (o: Partial<HistoryRow> = {}): HistoryRow => ({
  status: 'SUCCESS', total_return: 10, sharpe: 1.5, ...o,
})

describe('summarizeHistory', () => {
  it('空数组', () => {
    const k = summarizeHistory([])
    expect(k).toEqual({ total: 0, success: 0, successRate: 0, avgReturn: 0, bestSharpe: 0 })
  })
  it('成功率 / 平均收益 / 最佳 Sharpe', () => {
    const rows = [
      r({ status: 'SUCCESS', total_return: 20, sharpe: 2.0 }),
      r({ status: 'SUCCESS', total_return: -5, sharpe: 0.5 }),
      r({ status: 'FAILED', total_return: null, sharpe: null }),
      r({ status: 'RUNNING', total_return: 10, sharpe: 1.0 }),
    ]
    const k = summarizeHistory(rows)
    expect(k.total).toBe(4)
    expect(k.success).toBe(2)
    expect(k.successRate).toBe(50)
    expect(k.avgReturn).toBeCloseTo((20 + -5 + 10) / 3)
    expect(k.bestSharpe).toBe(2.0)
  })
  it('null 过滤', () => {
    const k = summarizeHistory([
      r({ total_return: null, sharpe: null }),
    ])
    expect(k.avgReturn).toBe(0)
    expect(k.bestSharpe).toBe(0)
  })
})
