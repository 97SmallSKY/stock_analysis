import http from './http'

// /api/reports/weekly

export interface WeeklyReport {
  start_date: string
  end_date: string
  lhb_total: number
  lhb_top_reasons: { reason: string; cnt: number }[]
  lhb_top_net: {
    ts_code: string; trade_date: string;
    reason: string | null; net_amount: number | null; score: number | null;
  }[]
  robot_summary: {
    robot_id: number; sells: number; buys: number;
    fees: number; entries: number; net_pnl: number;
  }[]
  backtest_top: {
    task_id: string; strategy_name: string;
    annual_return: number; sharpe_ratio: number;
    max_drawdown: number; win_rate: number;
    finished_at: string;
  }[]
  errors: string[]
}

export async function fetchWeeklyReport(end?: string, days = 7): Promise<WeeklyReport> {
  const params: Record<string, string | number> = { days }
  if (end) params.end = end
  const { data } = await http.get<WeeklyReport>('/api/reports/weekly', { params })
  return data
}

export async function generateWeeklyReport(days = 7): Promise<WeeklyReport> {
  const { data } = await http.post<WeeklyReport>('/api/reports/weekly/generate', null, {
    params: { days },
  })
  return data
}
