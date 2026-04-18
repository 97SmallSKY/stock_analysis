import http from './http'

// /api/backtest/compare

export interface CompareStrategy {
  name: string            // 显示名
  strategy_name: string   // 后端已注册策略 key
  params?: Record<string, unknown>
}

export interface CompareReq {
  start_date: string
  end_date: string
  initial_capital?: number
  strategies: CompareStrategy[]
  max_positions?: number
  per_position_pct?: number
  max_workers?: number
}

export interface StrategyRun {
  name: string
  ok: boolean
  error: string | null
  total_return: number
  annual_return: number
  sharpe_ratio: number
  max_drawdown: number
  win_rate: number
  trade_count: number
  final_assets: number
  duration_ms: number
}

export interface ComparisonReport {
  start_date: string
  end_date: string
  initial_capital: number
  runs: StrategyRun[]
  winner: string | null
}

export async function compareBacktests(req: CompareReq): Promise<ComparisonReport> {
  const { data } = await http.post<ComparisonReport>('/api/backtest/compare', req)
  return data
}

export async function listStrategies(): Promise<{ strategies: string[] }> {
  const { data } = await http.get<{ strategies: string[] }>('/api/backtest/strategies')
  return data
}

// P7.1 /api/backtest/history
export interface BacktestHistoryRow {
  task_id: string
  strategy_name: string
  start_date: string
  end_date: string
  initial_capital: number
  status: string           // PENDING / RUNNING / SUCCESS / FAILED
  progress: number
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  elapsed_seconds: number | null
  total_return: number | null
  sharpe: number | null
  max_drawdown: number | null
  n_trades: number | null
  win_rate: number | null
}

export interface BacktestHistoryResp {
  total: number
  rows: BacktestHistoryRow[]
}

export interface BacktestHistoryParams {
  limit?: number
  status?: string
  strategy_name?: string
}

export async function fetchBacktestHistory(
  params: BacktestHistoryParams = {},
): Promise<BacktestHistoryResp> {
  const { data } = await http.get<BacktestHistoryResp>('/api/backtest/history', { params })
  return data
}
