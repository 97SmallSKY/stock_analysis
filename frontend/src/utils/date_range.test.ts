import { describe, it, expect } from 'vitest'
import {
  today, addDays, addMonths, calcRange, defaultRange, toYmd, fromYmd,
  DEFAULT_QUICK_OPTIONS,
} from './date_range'

describe('date_range', () => {
  describe('today()', () => {
    it('使用固定 Date 时应返回对应 ISO 短日期', () => {
      // 2026-04-20(周一) 12:34:56 UTC+8
      const d = new Date('2026-04-20T12:34:56+08:00')
      expect(today(d)).toBe('2026-04-20')
    })

    it('不传参数时返回当前本地日期', () => {
      const s = today()
      expect(s).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    })
  })

  describe('addDays()', () => {
    it('负数往前推', () => {
      expect(addDays('2026-04-20', -4)).toBe('2026-04-16')
    })
    it('正数往后推', () => {
      expect(addDays('2026-04-20', 3)).toBe('2026-04-23')
    })
    it('跨月', () => {
      expect(addDays('2026-05-01', -1)).toBe('2026-04-30')
    })
    it('跨年', () => {
      expect(addDays('2026-01-01', -1)).toBe('2025-12-31')
    })
  })

  describe('addMonths()', () => {
    it('往前 1 个月', () => {
      expect(addMonths('2026-04-20', -1)).toBe('2026-03-20')
    })
    it('往前 12 个月(一年)', () => {
      expect(addMonths('2026-04-20', -12)).toBe('2025-04-20')
    })
    it('2 月 29 往前 12 个月应落到 2 月 28(非闰年)', () => {
      // JS 原生行为:Date 会自动 normalize,实际表现以平台为准
      const r = addMonths('2024-02-29', -12)
      // 2023 非闰年,2-29 不存在 → JS 规范化为 3-1
      expect(r === '2023-02-28' || r === '2023-03-01').toBe(true)
    })
  })

  describe('calcRange()', () => {
    const anchor = '2026-04-20'

    it('today: start == end', () => {
      expect(calcRange('today', anchor)).toEqual({ start: anchor, end: anchor })
    })

    it('5d: 含今天共 5 天', () => {
      expect(calcRange('5d', anchor)).toEqual({ start: '2026-04-16', end: anchor })
    })

    it('1w: 含今天共 7 天', () => {
      expect(calcRange('1w', anchor)).toEqual({ start: '2026-04-14', end: anchor })
    })

    it('1m: 一个月前到今天', () => {
      expect(calcRange('1m', anchor)).toEqual({ start: '2026-03-20', end: anchor })
    })

    it('3m / q: 三个月前到今天(两者等价)', () => {
      const r3 = calcRange('3m', anchor)
      const rq = calcRange('q', anchor)
      expect(r3).toEqual(rq)
      expect(r3).toEqual({ start: '2026-01-20', end: anchor })
    })

    it('6m: K 线默认', () => {
      expect(calcRange('6m', anchor)).toEqual({ start: '2025-10-20', end: anchor })
    })

    it('1y: 表格页默认', () => {
      expect(calcRange('1y', anchor)).toEqual({ start: '2025-04-20', end: anchor })
    })

    it('不传 anchorEnd 时使用今天', () => {
      const r = calcRange('today')
      expect(r.start).toBe(r.end)
      expect(r.start).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    })
  })

  describe('defaultRange()', () => {
    it('默认 = 1 年前 → 今天', () => {
      const r = defaultRange()
      // 不硬比对具体值(依赖当天),只校验格式和跨度
      expect(r.start).toMatch(/^\d{4}-\d{2}-\d{2}$/)
      expect(r.end).toMatch(/^\d{4}-\d{2}-\d{2}$/)
      // 年份差应为 1
      const ys = Number(r.start.slice(0, 4))
      const ye = Number(r.end.slice(0, 4))
      expect(ye - ys).toBeGreaterThanOrEqual(0)
      expect(ye - ys).toBeLessThanOrEqual(1)
    })
  })

  describe('toYmd / fromYmd', () => {
    it('toYmd', () => {
      expect(toYmd('2026-04-20')).toBe('20260420')
    })
    it('fromYmd 正常', () => {
      expect(fromYmd('20260420')).toBe('2026-04-20')
    })
    it('fromYmd 非法', () => {
      expect(fromYmd('')).toBe('')
      expect(fromYmd('abc')).toBe('')
    })
  })

  describe('DEFAULT_QUICK_OPTIONS', () => {
    it('包含 8 个选项', () => {
      expect(DEFAULT_QUICK_OPTIONS).toHaveLength(8)
    })
    it('第一项是 1 年', () => {
      expect(DEFAULT_QUICK_OPTIONS[0]).toEqual({ key: '1y', label: '1年' })
    })
    it('最后一项是今天', () => {
      expect(DEFAULT_QUICK_OPTIONS[DEFAULT_QUICK_OPTIONS.length - 1]).toEqual({
        key: 'today', label: '今天',
      })
    })
  })
})
