// 回测历史任务 KPI 聚合纯函数

export interface HistoryRow {
  status: string
  total_return: number | null
  sharpe: number | null
}

export interface HistoryKPI {
  total: number
  success: number
  successRate: number
  avgReturn: number
  bestSharpe: number
}

export function summarizeHistory(rows: HistoryRow[]): HistoryKPI {
  const total = rows.length
  const success = rows.filter((r) => r.status === 'SUCCESS').length
  const successRate = total ? (success / total) * 100 : 0
  const returns = rows
    .map((r) => r.total_return)
    .filter((v): v is number => v != null)
  const avgReturn = returns.length
    ? returns.reduce((a, b) => a + b, 0) / returns.length
    : 0
  const sharpes = rows
    .map((r) => r.sharpe)
    .filter((v): v is number => v != null)
  const bestSharpe = sharpes.length ? Math.max(...sharpes) : 0
  return { total, success, successRate, avgReturn, bestSharpe }
}
