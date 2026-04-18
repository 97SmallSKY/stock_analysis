import http from './http'

export interface KlineBar {
  date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  vol: number | null
}

export interface KlineResp {
  ts_code: string
  freq: 'daily' | 'weekly' | 'minute'
  rows: KlineBar[]
  empty_reason: string | null
  trade_date?: string | null
}

export async function fetchKlineDaily(
  ts_code: string, from?: string, to?: string, limit = 250,
): Promise<KlineResp> {
  const params: Record<string, string | number> = { ts_code, limit }
  if (from) params.from = from
  if (to) params.to = to
  const { data } = await http.get<KlineResp>('/api/kline/daily', { params })
  return data
}

export async function fetchKlineWeekly(
  ts_code: string, from?: string, to?: string, limit = 104,
): Promise<KlineResp> {
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
