import http from './http'
import { LIMIT_MAX_KLINE_DAILY, LIMIT_MAX_KLINE_WEEKLY, assertLimit } from './limits'

export interface KlineBar {
  date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  vol: number | null
}

export interface MacdData {
  diff: (number | null)[]
  dea: (number | null)[]
  bar: (number | null)[]
}

export interface DmaData {
  ma10: (number | null)[]
  ma50: (number | null)[]
}

export interface KlineIndicators {
  dma?: DmaData
  macd?: MacdData
}

export interface KlineResp {
  ts_code: string
  freq: 'daily' | 'weekly' | 'minute'
  rows: KlineBar[]
  empty_reason: string | null
  trade_date?: string | null
  indicators?: KlineIndicators
}

export async function fetchKlineDaily(
  ts_code: string, from?: string, to?: string, limit = 250,
  indicators?: string,
): Promise<KlineResp> {
  assertLimit('fetchKlineDaily', limit, LIMIT_MAX_KLINE_DAILY)
  const params: Record<string, string | number> = { ts_code, limit }
  if (from) params.from = from
  if (to) params.to = to
  if (indicators) params.indicators = indicators
  const { data } = await http.get<KlineResp>('/api/kline/daily', { params })
  return data
}

export async function fetchKlineWeekly(
  ts_code: string, from?: string, to?: string, limit = 104,
): Promise<KlineResp> {
  assertLimit('fetchKlineWeekly', limit, LIMIT_MAX_KLINE_WEEKLY)
  const params: Record<string, string | number> = { ts_code, limit }
  if (from) params.from = from
  if (to) params.to = to
  const { data } = await http.get<KlineResp>('/api/kline/weekly', { params })
  return data
}

export async function fetchKlineMinute(
  ts_code: string, trade_date: string,
): Promise<KlineResp> {
  const { data } = await http.get<KlineResp>('/api/kline/minute', {
    params: { ts_code, trade_date },
  })
  return data
}

// P19.1/P19.2 (2026-04-20): 查询某股票在某频率下的可用日期范围
export interface KlineDaterange {
  ts_code: string
  freq: 'daily' | 'weekly' | 'minute'
  min: string | null  // YYYY-MM-DD
  max: string | null
  count: number
  empty_reason: string | null
}

export async function fetchKlineDaterange(
  ts_code: string,
  freq: 'daily' | 'weekly' | 'minute' = 'daily',
): Promise<KlineDaterange> {
  const { data } = await http.get<KlineDaterange>('/api/kline/daterange', {
    params: { ts_code, freq },
  })
  return data
}
