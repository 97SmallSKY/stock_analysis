import { describe, it, expect } from 'vitest'
import { formatScoreDetail, labelOf, extractRadarData, SCORE_LABEL } from './score_detail'

describe('formatScoreDetail', () => {
  it('空/null 返回空串', () => {
    expect(formatScoreDetail(null)).toBe('')
    expect(formatScoreDetail(undefined)).toBe('')
    expect(formatScoreDetail({})).toBe('')
  })

  it('未知字段保留原名', () => {
    const out = formatScoreDetail({ unknown_x: 1, y: 2.5 })
    expect(out).toBe('unknown_x: 1\ny: 2.50')
  })

  it('嵌套对象递归缩进且使用中文标签', () => {
    // P19.8: fund -> 资金维度; net_pct -> 净买入占比
    const out = formatScoreDetail({
      fund: { net_pct: 80, bs_ratio: 60 },
      sentiment_coef: 1.0,
    })
    const lines = out.split('\n')
    expect(lines[0]).toBe('资金维度:')
    expect(lines[1]).toBe('  净买入占比: 80')
    expect(lines[2]).toBe('  买卖比: 60')
    expect(lines[3]).toBe('情绪系数: 1')
  })

  it('真实 score_detail 结构用中文标签,不含 [object Object]', () => {
    const out = formatScoreDetail({
      fund: { net_pct: 80, bs_ratio: 60, float_mv: 40, l_net_abs: 0, reason_rank: 50 },
      hist: { avg_chg_3d: 50, win_rate_5d: 50 },
      tech: { day_pct: 100, turnover_rate: 100 },
      weights: { fund: 0.35, hist: 0.25, tech: 0.4 },
      sentiment_coef: 1.0,
    })
    expect(out).toContain('资金维度:')
    expect(out).toContain('  净买入占比: 80')
    expect(out).toContain('权重配比:')
    expect(out).not.toContain('fund:\n')      // 不再是英文字段
    expect(out).not.toContain('[object Object]')
  })

  it('数组扁平化', () => {
    // 'tags' 不在字典,保留原名
    expect(formatScoreDetail({ tags: ['a', 'b', 'c'] })).toBe('tags: [a, b, c]')
  })

  it('字符串值保留原样', () => {
    expect(formatScoreDetail({ status: 'DONE' })).toBe('status: DONE')
  })
})

// ---------------------------------------------------------------------
// P19.8 labelOf
// ---------------------------------------------------------------------
describe('labelOf', () => {
  it('已知字段返回中文', () => {
    expect(labelOf('net_pct')).toBe('净买入占比')
    expect(labelOf('win_rate_5d')).toBe('5日胜率')
    expect(labelOf('fund')).toBe('资金维度')
  })
  it('未知字段返回原名', () => {
    expect(labelOf('foo_bar_xyz')).toBe('foo_bar_xyz')
  })
  it('字典至少收录 15 个字段', () => {
    expect(Object.keys(SCORE_LABEL).length).toBeGreaterThanOrEqual(15)
  })
})

// ---------------------------------------------------------------------
// P19.8 extractRadarData
// ---------------------------------------------------------------------
describe('extractRadarData', () => {
  it('null/空返回空结构', () => {
    expect(extractRadarData(null)).toEqual({ indicator: [], value: [], group: [] })
    expect(extractRadarData({})).toEqual({ indicator: [], value: [], group: [] })
  })

  it('抽取 fund/hist/tech 三组数值,忽略 weights/coef', () => {
    const r = extractRadarData({
      fund: { net_pct: 80, bs_ratio: 60 },
      hist: { win_rate_5d: 55 },
      tech: { day_pct: 90, volume_ratio: 70 },
      weights: { fund: 0.35, hist: 0.25, tech: 0.4 },  // 应被忽略
      sentiment_coef: 1.0,                              // 应被忽略
    })
    expect(r.indicator.length).toBe(5)
    expect(r.indicator[0].name).toBe('净买入占比')
    expect(r.value).toEqual([80, 60, 55, 90, 70])
    expect(r.group).toEqual([0, 0, 1, 2, 2])
  })

  it('clamp 到 [0, 100]', () => {
    const r = extractRadarData({
      fund: { net_pct: 150, bs_ratio: -20 },
    })
    expect(r.value).toEqual([100, 0])
  })

  it('非数值子项被忽略', () => {
    const r = extractRadarData({
      fund: { net_pct: 50, bs_ratio: 'ABC' as unknown as number },
    })
    expect(r.value).toEqual([50])
    expect(r.indicator.length).toBe(1)
  })
})
