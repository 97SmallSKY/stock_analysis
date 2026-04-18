import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('./http', () => ({
  default: { get: vi.fn() },
}))

import http from './http'
import { fetchKlineDaily, fetchKlineWeekly, fetchKlineMinute } from './kline'

const getMock = http.get as unknown as ReturnType<typeof vi.fn>

beforeEach(() => { getMock.mockReset() })

describe('fetchKlineDaily', () => {
  it('带 from/to 参数, 返回 data', async () => {
    getMock.mockResolvedValue({
      data: { ts_code: '000001.SZ', freq: 'daily', rows: [], empty_reason: null },
    })
    const r = await fetchKlineDaily('000001.SZ', '20260101', '20260131')
    expect(r.freq).toBe('daily')
    const [url, cfg] = getMock.mock.calls[0]
    expect(url).toBe('/api/kline/daily')
    expect(cfg.params).toMatchObject({
      ts_code: '000001.SZ', from: '20260101', to: '20260131',
    })
  })

  it('不传日期只带 ts_code + limit', async () => {
    getMock.mockResolvedValue({ data: { ts_code: 'X', freq: 'daily', rows: [], empty_reason: null } })
    await fetchKlineDaily('000002.SZ')
    const cfg = getMock.mock.calls[0][1]
    expect(cfg.params.ts_code).toBe('000002.SZ')
    expect(cfg.params.limit).toBe(250)
    expect(cfg.params.from).toBeUndefined()
  })
})

describe('fetchKlineWeekly', () => {
  it('默认 limit=104', async () => {
    getMock.mockResolvedValue({ data: { ts_code: 'X', freq: 'weekly', rows: [], empty_reason: null } })
    await fetchKlineWeekly('600000.SH')
    const cfg = getMock.mock.calls[0][1]
    expect(cfg.params.limit).toBe(104)
  })
})

describe('fetchKlineMinute', () => {
  it('trade_date 直传', async () => {
    getMock.mockResolvedValue({
      data: { ts_code: '000001.SZ', freq: 'minute', rows: [], empty_reason: 'no_source_table' },
    })
    const r = await fetchKlineMinute('000001.SZ', '20260418')
    expect(r.empty_reason).toBe('no_source_table')
    const cfg = getMock.mock.calls[0][1]
    expect(cfg.params).toEqual({ ts_code: '000001.SZ', trade_date: '20260418' })
  })
})
