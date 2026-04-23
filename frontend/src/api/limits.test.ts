/**
 * STO-62: 前端 limit 边界校验测试
 *
 * 覆盖场景:
 *   1. 边界内(limit < max) 通过
 *   2. 等于上限(limit = max) 通过
 *   3. 超上限(limit > max) 抛 LimitOutOfRangeError
 *   4. undefined 不校验(让默认值生效)
 *   5. fetchLhb 调用方超限也抛错
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  LIMIT_MAX_LHB,
  LIMIT_MAX_LHB_SEATS,
  LIMIT_MAX_KLINE_DAILY,
  LIMIT_MAX_KLINE_WEEKLY,
  LimitOutOfRangeError,
  assertLimit,
} from './limits'

describe('limits.ts 常量', () => {
  it('4 个常量与后端 le= 对齐', () => {
    expect(LIMIT_MAX_LHB).toBe(1000)
    expect(LIMIT_MAX_LHB_SEATS).toBe(2000)
    expect(LIMIT_MAX_KLINE_DAILY).toBe(5000)
    expect(LIMIT_MAX_KLINE_WEEKLY).toBe(2000)
  })
})

describe('assertLimit', () => {
  it('limit < max 通过', () => {
    expect(() => assertLimit('X', 500, 1000)).not.toThrow()
  })

  it('limit === max 通过(等于上限不抛错)', () => {
    expect(() => assertLimit('X', 1000, 1000)).not.toThrow()
  })

  it('limit > max 抛 LimitOutOfRangeError', () => {
    expect(() => assertLimit('X', 1001, 1000)).toThrow(LimitOutOfRangeError)
  })

  it('limit === undefined 不校验(允许 fetchXxx 用默认值)', () => {
    expect(() => assertLimit('X', undefined, 1000)).not.toThrow()
  })

  it('limit < 1 也抛错', () => {
    expect(() => assertLimit('X', 0, 1000)).toThrow(LimitOutOfRangeError)
    expect(() => assertLimit('X', -5, 1000)).toThrow(LimitOutOfRangeError)
  })

  it('NaN/Infinity 抛错', () => {
    expect(() => assertLimit('X', NaN, 1000)).toThrow(LimitOutOfRangeError)
    expect(() => assertLimit('X', Infinity, 1000)).toThrow(LimitOutOfRangeError)
  })
})

// ---- 集成:fetchLhb 超限是否真抛错 ----
vi.mock('./http', () => ({
  default: { get: vi.fn() },
}))
import http from './http'
import { fetchLhb } from './lhb'
import { fetchKlineDaily } from './kline'

const getMock = http.get as unknown as ReturnType<typeof vi.fn>
beforeEach(() => { getMock.mockReset() })

describe('fetchLhb limit enforcement (STO-62)', () => {
  it('limit=1001 抛错,不发 http 请求', async () => {
    await expect(fetchLhb({ limit: 1001 })).rejects.toThrow(LimitOutOfRangeError)
    expect(getMock).not.toHaveBeenCalled()
  })

  it('limit=1000(上限)正常发请求', async () => {
    getMock.mockResolvedValue({ data: { total: 0, limit: 1000, offset: 0, rows: [] } })
    await fetchLhb({ limit: 1000 })
    expect(getMock).toHaveBeenCalledOnce()
  })
})

describe('fetchKlineDaily limit enforcement (STO-62)', () => {
  it('limit=5001 抛错', async () => {
    await expect(fetchKlineDaily('000001.SZ', undefined, undefined, 5001))
      .rejects.toThrow(LimitOutOfRangeError)
    expect(getMock).not.toHaveBeenCalled()
  })
})
