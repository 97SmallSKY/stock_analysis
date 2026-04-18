import http from './http'

// 与 backend/api/lhb.py 对齐
export interface LhbRow {
  id?: number
  ts_code: string
  trade_date: string | null
  reason: string | null
  close: number | null
  pct_change: number | null
  turnover: number | null
  buy_amount: number | null
  sell_amount: number | null
  net_amount: number | null
  score: number | null
  score_detail: Record<string, unknown> | null
}

export interface LhbListResp {
  total: number
  limit: number
  offset: number
  rows: LhbRow[]
}

export interface LhbListParams {
  date_from?: string
  date_to?: string
  ts_code?: string
  reason?: string
  net_amount_min?: number
  score_min?: number
  order_by?: 'trade_date' | 'score' | 'net_amount' | 'pct_change'
  order_desc?: boolean
  limit?: number
  offset?: number
}

export async function fetchLhb(params: LhbListParams = {}): Promise<LhbListResp> {
  const { data } = await http.get<LhbListResp>('/api/lhb', { params })
  return data
}

export interface LhbSeatRow {
  ts_code: string
  trade_date: string | null
  direction: 'buy' | 'sell' | string
  seat_name: string
  buy_amount: number | null
  sell_amount: number | null
  net_amount: number | null
  rank: number | null
}

export interface LhbSeatParams {
  trade_date: string
  ts_code: string
  direction?: 'buy' | 'sell'
  limit?: number
}

export async function fetchLhbSeats(params: LhbSeatParams): Promise<{ rows: LhbSeatRow[] }> {
  const { data } = await http.get<{ rows: LhbSeatRow[] }>('/api/lhb/seats', { params })
  return data
}
