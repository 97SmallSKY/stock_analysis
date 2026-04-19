import { describe, it, expect } from 'vitest'
import {
  fmtAmount, fmtPct, fmtNumber, fmtDate, healthColor, cbColor, rankClass,
  fmtAmountWan,
} from './format'

describe('fmtAmount', () => {
  it('null/undefined/NaN -> -', () => {
    expect(fmtAmount(null)).toBe('-')
    expect(fmtAmount(undefined)).toBe('-')
    expect(fmtAmount(NaN)).toBe('-')
  })
  it('亿级格式化', () => {
    expect(fmtAmount(2.5e8)).toBe('2.50 亿')
    expect(fmtAmount(-1.2e8)).toBe('-1.20 亿')
  })
  it('万级格式化', () => {
    expect(fmtAmount(35000)).toBe('3.50 万')
  })
  it('小额原样', () => {
    expect(fmtAmount(123.456)).toBe('123.46')
  })
})

describe('fmtPct', () => {
  it('百分比默认 2 位', () => {
    expect(fmtPct(3.14159)).toBe('3.14%')
    expect(fmtPct(-1)).toBe('-1.00%')
  })
  it('指定位数', () => {
    expect(fmtPct(1.2345, 3)).toBe('1.234%')
  })
  it('空值', () => {
    expect(fmtPct(null)).toBe('-')
  })
})

describe('fmtNumber / fmtDate', () => {
  it('数字', () => {
    expect(fmtNumber(42)).toBe('42')
    expect(fmtNumber(null)).toBe('-')
  })
  it('日期截取 YYYY-MM-DD', () => {
    expect(fmtDate('2025-01-15T00:00:00')).toBe('2025-01-15')
    expect(fmtDate('2025-01-15')).toBe('2025-01-15')
    expect(fmtDate(null)).toBe('-')
    expect(fmtDate('')).toBe('-')
  })
})

describe('healthColor', () => {
  it('true=绿, false=红, null=灰', () => {
    expect(healthColor(true)).toBe('#52c41a')
    expect(healthColor(false)).toBe('#f5222d')
    expect(healthColor(null)).toBe('#d9d9d9')
    expect(healthColor(undefined)).toBe('#d9d9d9')
  })
})

describe('cbColor', () => {
  it('closed=green half_open=gold open=red', () => {
    expect(cbColor('closed')).toBe('green')
    expect(cbColor('half_open')).toBe('gold')
    expect(cbColor('open')).toBe('red')
    expect(cbColor(null)).toBe('default')
    expect(cbColor('foo')).toBe('default')
  })
})

describe('fmtAmountWan (单位已是万元)', () => {
  it('null/undefined/NaN -> -', () => {
    expect(fmtAmountWan(null)).toBe('-')
    expect(fmtAmountWan(undefined)).toBe('-')
    expect(fmtAmountWan(NaN)).toBe('-')
  })
  it('<1亿 保留万, >=1亿 (>=10000万) 转亿', () => {
    expect(fmtAmountWan(5811.81)).toBe('5811.81 万')
    expect(fmtAmountWan(9999)).toBe('9999.00 万')
    expect(fmtAmountWan(10000)).toBe('1.00 亿')
    expect(fmtAmountWan(25000)).toBe('2.50 亿')
  })
  it('负数', () => {
    expect(fmtAmountWan(-7877.73)).toBe('-7877.73 万')
    expect(fmtAmountWan(-12345)).toBe('-1.23 亿')
  })
  it('0 保留', () => {
    expect(fmtAmountWan(0)).toBe('0.00 万')
  })
})

describe('rankClass', () => {
  it('1/2/3 专属色, 4+ 灰, null 空串', () => {
    expect(rankClass(1)).toBe('rank-1')
    expect(rankClass(2)).toBe('rank-2')
    expect(rankClass(3)).toBe('rank-3')
    expect(rankClass(4)).toBe('')
    expect(rankClass(99)).toBe('')
    expect(rankClass(null)).toBe('')
    expect(rankClass(undefined)).toBe('')
  })
})
