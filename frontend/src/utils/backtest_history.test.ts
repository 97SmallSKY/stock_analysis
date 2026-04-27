import { describe, it, expect, vi } from 'vitest'
import { summarizeHistory, buildHistoryRowClickHandler, type HistoryRow } from './backtest_history'

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

describe('buildHistoryRowClickHandler (STO-101)', () => {
  it('点击空白处导航,navigate 收到 task_id', () => {
    const navigate = vi.fn()
    const handler = buildHistoryRowClickHandler(navigate)
    const result = handler('abc-123', null)
    expect(result).toBe(true)
    expect(navigate).toHaveBeenCalledWith('abc-123')
  })
  it('点击 div 元素 (无 a/button 祖先) 导航', () => {
    const navigate = vi.fn()
    const handler = buildHistoryRowClickHandler(navigate)
    const div = document.createElement('div')
    expect(handler('xyz', div)).toBe(true)
    expect(navigate).toHaveBeenCalledWith('xyz')
  })
  it('点中 <a> 不导航 (让链接原生处理)', () => {
    const navigate = vi.fn()
    const handler = buildHistoryRowClickHandler(navigate)
    const a = document.createElement('a')
    expect(handler('xyz', a)).toBe(false)
    expect(navigate).not.toHaveBeenCalled()
  })
  it('点中 <button> 不导航', () => {
    const navigate = vi.fn()
    const handler = buildHistoryRowClickHandler(navigate)
    const btn = document.createElement('button')
    expect(handler('xyz', btn)).toBe(false)
    expect(navigate).not.toHaveBeenCalled()
  })
  it('点中 <a> 内部 span 也不导航 (closest 命中)', () => {
    const navigate = vi.fn()
    const handler = buildHistoryRowClickHandler(navigate)
    const a = document.createElement('a')
    const span = document.createElement('span')
    a.appendChild(span)
    expect(handler('xyz', span)).toBe(false)
    expect(navigate).not.toHaveBeenCalled()
  })
})
