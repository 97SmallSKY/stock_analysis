import { describe, it, expect } from 'vitest'
import { GLOSSARY, findTerm, termsByTag } from './glossary'

describe('glossary', () => {
  it('至少收录 20 个术语', () => {
    expect(GLOSSARY.length).toBeGreaterThanOrEqual(20)
  })

  it('每个术语都有 key/name/tag/brief', () => {
    for (const t of GLOSSARY) {
      expect(t.key).toBeTruthy()
      expect(t.name).toBeTruthy()
      expect(t.tag).toBeTruthy()
      expect(t.brief).toBeTruthy()
      // tag 合法枚举
      expect(['lhb', 'score', 'sentiment', 'backtest', 'market']).toContain(t.tag)
    }
  })

  it('key 全局唯一', () => {
    const keys = GLOSSARY.map((t) => t.key)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it('findTerm 按 key 查', () => {
    const t = findTerm('hot_money')
    expect(t).not.toBeNull()
    expect(t!.name).toBe('游资')
  })

  it('findTerm 按中文名查', () => {
    expect(findTerm('涨停')?.key).toBe('limit_up')
  })

  it('findTerm 按别名查', () => {
    expect(findTerm('Sharpe')?.key).toBe('sharpe')
    expect(findTerm('hot money')?.key).toBe('hot_money')
  })

  it('findTerm 大小写不敏感', () => {
    expect(findTerm('HOT_MONEY')?.key).toBe('hot_money')
    expect(findTerm('sharpe')?.key).toBe('sharpe')
  })

  it('findTerm 找不到返回 null', () => {
    expect(findTerm('nonexistent_xyz')).toBeNull()
    expect(findTerm('')).toBeNull()
  })

  it('termsByTag 筛选有效', () => {
    const lhbTerms = termsByTag('lhb')
    expect(lhbTerms.length).toBeGreaterThan(0)
    for (const t of lhbTerms) expect(t.tag).toBe('lhb')

    const scoreTerms = termsByTag('score')
    expect(scoreTerms.length).toBeGreaterThan(0)

    const sentimentTerms = termsByTag('sentiment')
    expect(sentimentTerms.length).toBeGreaterThan(0)

    const btTerms = termsByTag('backtest')
    expect(btTerms.length).toBeGreaterThan(0)
  })

  it('关键术语存在', () => {
    // 用户需求里重点提到的
    const mustHave = [
      'lhb', 'hot_money', 'institution', 'net_amount', 'score',
      'win_rate', 'turnover_rate', 'limit_up', 'cont_limit_up',
      'up_down_ratio', 'median_pct_change', 'sharpe', 'max_drawdown',
    ]
    for (const k of mustHave) {
      expect(findTerm(k), `missing term: ${k}`).not.toBeNull()
    }
  })
})
