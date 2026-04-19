import http from './http'

// 与 backend/api/dashboard.py 返回结构对齐
export interface Sentiment {
  trade_date: string | null
  total_stocks: number | null
  up_count: number | null
  down_count: number | null
  limit_up_count: number | null
  limit_down_count: number | null
  avg_pct_change: number | null
  sentiment_score: number | null
}

export interface TopLhbRow {
  ts_code: string
  name: string | null    // P10.1 JOIN stock_info
  trade_date: string | null
  reason: string | null
  net_amount: number | null
  buy_amount: number | null
  sell_amount: number | null
  score: number | null
}

export interface AdapterStatus {
  name: string
  initialized: boolean
  circuit_state: string | null
  last_health_ok: boolean | null
  last_latency_ms: number | null
}

export interface DashboardResp {
  sentiment: Sentiment | null
  top_lhb: TopLhbRow[]
  adapters: AdapterStatus[]
}

export async function fetchDashboard(topN = 10): Promise<DashboardResp> {
  const { data } = await http.get<DashboardResp>('/api/dashboard', {
    params: { top_n: topN },
  })
  return data
}
