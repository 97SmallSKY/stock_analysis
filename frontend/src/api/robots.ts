import http from './http'

// 与 backend/api/robots.py 对齐
export interface RobotPosition {
  ts_code: string
  shares: number
  cost_price: number | null
  current_price: number | null
  market_value: number | null
  open_date: string | null
}

export interface RobotSnapshot {
  robot_id: number
  state: string // RUNNING / PAUSED / IDLE / STOPPED
  cash: number
  total_assets: number
  daily_pnl_pct: number | null
  position_count: number
  enabled?: boolean
  positions: RobotPosition[]
}

export async function listRobots(): Promise<{ robots: RobotSnapshot[] }> {
  const { data } = await http.get<{ robots: RobotSnapshot[] }>('/api/robots')
  return data
}

export async function getRobotStatus(id: number): Promise<{ ok: boolean; snapshot: RobotSnapshot }> {
  const { data } = await http.get(`/api/robots/${id}/status`)
  return data
}

async function post(id: number, action: 'pause' | 'resume' | 'enable' | 'disable') {
  const { data } = await http.post(`/api/robots/${id}/${action}`)
  return data as { ok: boolean; snapshot: RobotSnapshot }
}

export const pauseRobot = (id: number) => post(id, 'pause')
export const resumeRobot = (id: number) => post(id, 'resume')
export const enableRobot = (id: number) => post(id, 'enable')
export const disableRobot = (id: number) => post(id, 'disable')
