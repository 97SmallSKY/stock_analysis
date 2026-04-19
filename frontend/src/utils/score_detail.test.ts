import { describe, it, expect } from 'vitest'
import { formatScoreDetail } from './score_detail'

describe('formatScoreDetail', () => {
  it('空/null 返回空串', () => {
    expect(formatScoreDetail(null)).toBe('')
    expect(formatScoreDetail(undefined)).toBe('')
    expect(formatScoreDetail({})).toBe('')
  })

  it('平铺数字', () => {
    const out = formatScoreDetail({ a: 1, b: 2.5 })
    expect(out).toBe('a: 1\nb: 2.50')
  })

  it('嵌套对象递归缩进', () => {
    const out = formatScoreDetail({
      fund: { net_pct: 80, bs_ratio: 60 },
      sentiment_coef: 1.0,
    })
    const lines = out.split('\n')
    expect(lines[0]).toBe('fund:')
    expect(lines[1]).toBe('  net_pct: 80')
    expect(lines[2]).toBe('  bs_ratio: 60')
    expect(lines[3]).toBe('sentiment_coef: 1')
  })

  it('真实 score_detail 结构不包含 [object Object]', () => {
    const out = formatScoreDetail({
      fund: { net_pct: 80, bs_ratio: 60, float_mv: 40, l_net_abs: 0, reason_rank: 50 },
      hist: { avg_chg_3d: 50, win_rate_5d: 50 },
      tech: { day_pct: 100, turnover_rate: 100 },
      weights: { fund: 0.35, hist: 0.25, tech: 0.4 },
      sentiment_coef: 1.0,
    })
    expect(out).toContain('fund:')
    expect(out).toContain('  net_pct: 80')
    expect(out).toContain('weights:')
    expect(out).toContain('  fund: 0.35')
    expect(out).not.toContain('[object Object]')
  })

  it('数组扁平化', () => {
    expect(formatScoreDetail({ tags: ['a', 'b', 'c'] })).toBe('tags: [a, b, c]')
  })

  it('字符串值保留原样', () => {
    expect(formatScoreDetail({ status: 'DONE' })).toBe('status: DONE')
  })
})
