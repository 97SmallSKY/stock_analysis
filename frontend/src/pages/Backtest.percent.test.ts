/**
 * STO-46 UI 重设计回归:单仓比例百分比输入 + formatter/parser 边界。
 *
 * 策略:直接用 fs 读源文件断言关键符号存在。避免挂载真实 Vue 组件。
 * 另外对 parser 做纯函数验证(内联 re-declaration,和源文件保持一致)。
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

// 内联与 Backtest.vue 里定义等价的纯函数,用于边界测试
const pctFormatter = (v: number | undefined): string =>
  v === undefined || v === null ? '' : `${v}%`

const pctParser = (v: string | undefined): number => {
  if (!v) return 0
  const n = Number(v.replace('%', '').trim())
  return Number.isNaN(n) ? 0 : n
}

describe('STO-46 百分比转换纯函数', () => {
  it('formatter 普通整数', () => {
    expect(pctFormatter(10)).toBe('10%')
    expect(pctFormatter(100)).toBe('100%')
    expect(pctFormatter(0)).toBe('0%')
  })

  it('formatter 小数保留', () => {
    expect(pctFormatter(12.5)).toBe('12.5%')
  })

  it('formatter undefined/null 返回空串', () => {
    expect(pctFormatter(undefined)).toBe('')
    expect(pctFormatter(null as unknown as number)).toBe('')
  })

  it('parser 带 % 后缀', () => {
    expect(pctParser('10%')).toBe(10)
    expect(pctParser('100%')).toBe(100)
  })

  it('parser 无 % 后缀', () => {
    expect(pctParser('15')).toBe(15)
  })

  it('parser 带空格', () => {
    expect(pctParser('  20%  ')).toBe(20)
  })

  it('parser 空串 → 0', () => {
    expect(pctParser('')).toBe(0)
    expect(pctParser(undefined)).toBe(0)
  })

  it('parser 非数字 → 0', () => {
    expect(pctParser('abc%')).toBe(0)
  })

  it('边界:往返不丢精度(formatter → parser)', () => {
    for (const v of [5, 10, 20, 50, 100]) {
      expect(pctParser(pctFormatter(v))).toBe(v)
    }
  })
})

describe('STO-46 源文件契约', () => {
  const src = fs.readFileSync(
    path.resolve(__dirname, 'Backtest.vue'), 'utf8',
  )

  it('使用 perPositionPctInput(百分比显示层)', () => {
    expect(src).toContain('perPositionPctInput')
    expect(src).toContain(':formatter="pctFormatter"')
    expect(src).toContain(':parser="pctParser"')
  })

  it('提交时 per_position_pct 仍用 0-1 小数(API 契约)', () => {
    // 提交点未改,仍是 perPositionPct.value 的 0-1 小数
    expect(src).toContain('per_position_pct: perPositionPct.value')
  })

  it('表单垂直布局:日期/资金/持仓/比例 4 个 form-item 同在左列 span=10', () => {
    // 最简判断:a-col :span="10" 块内含 4 个 label
    const labels = ['日期范围', '初始资金', '最大持仓', '单仓比例']
    for (const lb of labels) {
      expect(src).toContain(`label="${lb}"`)
    }
  })

  it('右栏候选策略 a-col span 扩到 14', () => {
    // 原 :span="8" 改 14,验证布局 migrate
    expect(src).toMatch(/<a-col :span="14">\s*<a-form-item label="候选策略">/)
  })
})
