import http from './http'

// 与 backend/api/lhb.py 对齐
export interface LhbRow {
  id?: number
  ts_code: string
  name: string | null    // P10.1 JOIN stock_info
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
  // P16.5: 仅当 with_tn=true 时存在
  t1_pnl_pct?: number | null
  t1_limit_up?: boolean | null
  t5_pnl_pct?: number | null
  t5_high_pct?: number | null
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
  // P16.5: 透出 t1/t5 次日收益字段
  with_tn?: boolean
  // STO-43: 跳过 COUNT(*),total 返回 -1,首屏显示 "已加载 N 条" 即可
  skip_total?: boolean
}

export async function fetchLhb(params: LhbListParams = {}): Promise<LhbListResp> {
  const { data } = await http.get<LhbListResp>('/api/lhb', { params })
  return data
}

export interface LhbSeatRow {
  ts_code: string
  name: string | null   // P10.1 JOIN stock_info
  trade_date: string | null
  direction: 'BUY' | 'SELL' | string
  broker_name: string   // 对齐后端实际字段名
  buy_amount: number | null
  sell_amount: number | null
  net_amount: number | null
  rank_no: number | null  // 对齐后端 AS 别名 (SQL 'rank' 是关键字)
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
