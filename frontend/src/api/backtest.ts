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
