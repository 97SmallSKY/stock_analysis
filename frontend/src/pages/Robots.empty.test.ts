/**
 * STO-36 回归:Robots / BacktestRun 空态 CTA 文案存在。
 *
 * 策略(避免 a-table/ant-design 的全量依赖):
 *   - 直接 grep template 源文本,断言「创建第一个机器人」/「跑第一个回测」关键词存在
 *   - 之后若重构改文案,此测试红线提醒
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

describe('STO-36 空态 CTA 文案', () => {
  it('Robots.vue 包含「创建第一个机器人」CTA', () => {
    const src = fs.readFileSync(
      path.resolve(__dirname, 'Robots.vue'), 'utf8',
    )
    expect(src).toContain('创建第一个机器人')
    expect(src).toContain('/changelog')
  })

  it('BacktestRun.vue 包含「跑第一个回测」CTA', () => {
    const src = fs.readFileSync(
      path.resolve(__dirname, 'BacktestRun.vue'), 'utf8',
    )
    expect(src).toContain('跑第一个回测')
    expect(src).toContain("router.push('/backtest')")
    expect(src).toContain('/changelog')
  })

  it('Robots 空态还保留对非空 a-table 的条件分支(回归)', () => {
    const src = fs.readFileSync(
      path.resolve(__dirname, 'Robots.vue'), 'utf8',
    )
    // robots.length === 0 → 显示 empty;否则(v-else)才渲染 a-table
    expect(src).toMatch(/robots\.length === 0/)
    expect(src).toMatch(/<a-table[\s\S]*?v-else/)
  })

  it('BacktestRun 非空时渲染 table(回归)', () => {
    const src = fs.readFileSync(
      path.resolve(__dirname, 'BacktestRun.vue'), 'utf8',
    )
    expect(src).toMatch(/historyRows\.length === 0/)
    expect(src).toMatch(/<a-table[\s\S]*?v-else/)
  })
})
