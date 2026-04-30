import { describe, it, expect } from 'vitest'
import {
  fmtAmount, fmtPct, fmtFractionPct, fmtNumber, fmtDate, healthColor, cbColor, rankClass,
  fmtAmountWan, formatPrice,
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

describe('fmtFractionPct (STO-102)', () => {
  it('分数 → 百分比 (×100)', () => {
    expect(fmtFractionPct(0.2591)).toBe('25.91%')
    expect(fmtFractionPct(0.3077)).toBe('30.77%')
    expect(fmtFractionPct(-0.2591)).toBe('-25.91%')
  })
  it('0 / 1 边界', () => {
    expect(fmtFractionPct(0)).toBe('0.00%')
    expect(fmtFractionPct(1)).toBe('100.00%')
  })
  it('指定位数', () => {
    expect(fmtFractionPct(0.12345, 3)).toBe('12.345%')
  })
  it('空值 → -', () => {
    expect(fmtFractionPct(null)).toBe('-')
    expect(fmtFractionPct(undefined)).toBe('-')
    expect(fmtFractionPct(NaN)).toBe('-')
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
  it('lowercase: closed=green half_open=gold open=red', () => {
    expect(cbColor('closed')).toBe('green')
    expect(cbColor('half_open')).toBe('gold')
    expect(cbColor('open')).toBe('red')
    expect(cbColor(null)).toBe('default')
    expect(cbColor('foo')).toBe('default')
  })
  it('uppercase backend values: CLOSED=green HALF_OPEN=gold OPEN=red', () => {
    expect(cbColor('CLOSED')).toBe('green')
    expect(cbColor('HALF_OPEN')).toBe('gold')
    expect(cbColor('OPEN')).toBe('red')
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

// STO-30: 股票价格自适应精度
describe('formatPrice', () => {
  it('null/undefined/NaN -> -', () => {
    expect(formatPrice(null, 10)).toBe('-')
    expect(formatPrice(undefined, 10)).toBe('-')
    expect(formatPrice(NaN, 10)).toBe('-')
  })

  it('maxVal < 10 → 3 位小数(低价股)', () => {
    // 920xxx 北交所 ST 股场景
    expect(formatPrice(1.36, 1.36)).toBe('1.360')
    expect(formatPrice(1.2456, 1.36)).toBe('1.246')
    expect(formatPrice(0.5, 1.36)).toBe('0.500')
  })

  it('10 <= maxVal < 100 → 2 位小数(主流)', () => {
    expect(formatPrice(12.35, 50)).toBe('12.35')
    expect(formatPrice(12.345, 50)).toBe('12.35')
    expect(formatPrice(50, 50)).toBe('50.00')
  })

  it('maxVal >= 100 → 1 位小数(高价股)', () => {
    expect(formatPrice(158.7, 200)).toBe('158.7')
    expect(formatPrice(158.76, 200)).toBe('158.8')
    expect(formatPrice(100, 100)).toBe('100.0')
  })

  it('maxVal 决定档位,val 本身可跨档(整档一致性)', () => {
    expect(formatPrice(0.5, 150)).toBe('0.5')
    expect(formatPrice(150, 150)).toBe('150.0')
  })

  it('负数(跌幅/负收益)也按档位 format,保留符号', () => {
    expect(formatPrice(-1.234, 1.5)).toBe('-1.234')
    expect(formatPrice(-12.35, 50)).toBe('-12.35')
  })

  it('maxVal 负数(异常输入)按绝对值决定档位', () => {
    expect(formatPrice(1.5, -5)).toBe('1.500')
  })
})
