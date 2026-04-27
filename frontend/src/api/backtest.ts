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

// STO-46: compare 跑 N 个策略耗时可到几十秒,覆盖全局 10s timeout
// (不改全局 http.ts 默认,仅此端点放宽到 3 分钟)
export const COMPARE_TIMEOUT_MS = 180_000

export async function compareBacktests(req: CompareReq): Promise<ComparisonReport> {
  const { data } = await http.post<ComparisonReport>('/api/backtest/compare', req, {
    timeout: COMPARE_TIMEOUT_MS,
  })
  return data
}

/** STO-47: 策略富信息 (后端 /api/backtest/strategies 升级后返回)。 */
export interface StrategyInfo {
  name: string                        // 策略英文代号, 如 lhb_hot_money
  category: string                    // LHB / MOMENTUM / CUSTOM ...
  description: string                 // 中文描述, 空串时 UI fallback 到 name
  default_params: Record<string, unknown>
}

export async function listStrategies(): Promise<{ strategies: StrategyInfo[] }> {
  const { data } = await http.get<{ strategies: StrategyInfo[] }>(
    '/api/backtest/strategies',
  )
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

// STO-89 / STO-80b: 提交单策略回测任务 (POST /api/backtest)
export interface SubmitBacktestReq {
  strategy_name: string
  start_date: string             // YYYY-MM-DD
  end_date: string
  initial_capital?: number
  params?: Record<string, unknown>
  max_positions?: number
  per_position_pct?: number
}

export interface SubmitBacktestResp {
  task_id: string
  status: string
}

export async function submitBacktest(
  req: SubmitBacktestReq,
): Promise<SubmitBacktestResp> {
  const { data } = await http.post<SubmitBacktestResp>('/api/backtest', req)
  return data
}

// STO-90 / STO-80c: 回测任务详情 (GET /api/backtest/{task_id})
export interface NavPoint {
  date: string                    // YYYY-MM-DD
  nav: number                     // 相对初始资金单位净值,起点 1.0
  total_assets: number
  cash: number
  position_value: number
  position_count: number
  pnl_daily: number
  pnl_daily_pct: number
  trade_count: number
  buy_amount: number
  sell_amount: number
}

export interface BacktestTaskDetail {
  task_id: string
  strategy_name: string
  start_date: string
  end_date: string
  initial_capital: number
  status: string
  progress: number
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  elapsed_seconds: number | null
  params: Record<string, unknown>
  result: {
    final_assets?: number
    total_return?: number
    trade_count?: number
    nav_series?: NavPoint[]
  } | null
  metrics: {
    total_return?: number
    annual_return?: number
    sharpe_ratio?: number
    max_drawdown?: number
    win_rate?: number
    n_trades?: number
  } | null
}

export async function fetchBacktestTask(
  taskId: string,
): Promise<BacktestTaskDetail> {
  const { data } = await http.get<BacktestTaskDetail>(`/api/backtest/${encodeURIComponent(taskId)}`)
  return data
}
