import http from './http'

// 与 backend/api/data_sources.py 对齐
export interface CircuitStatus {
  state: string              // closed / half_open / open
  failure_count: number
  last_failure_at: string | null
  [k: string]: unknown
}

export interface HealthSnap {
  is_healthy: boolean
  latency_ms: number | null
  checked_at: string | null
  error: string | null
  [k: string]: unknown
}

export interface AdapterInfo {
  name: string
  initialized: boolean
  circuit: CircuitStatus
  last_health: HealthSnap | null
}

export interface DataSourceStatus {
  adapters: AdapterInfo[]
  // { DAILY_KLINE: ['akshare', 'sina'], ... }
  priority_chains: Record<string, string[]>
}

export async function fetchStatus(): Promise<DataSourceStatus> {
  const { data } = await http.get<DataSourceStatus>('/api/data-sources/status')
  return data
}

export async function setPriority(category: string, chain: string[]) {
  const { data } = await http.post('/api/data-sources/priority', { category, chain })
  return data
}

export async function retryAdapter(name: string) {
  const { data } = await http.post(`/api/data-sources/adapter/${name}/retry`)
  return data
}

export async function circuitAction(name: string, action: 'open' | 'close') {
  const { data } = await http.post(`/api/data-sources/adapter/${name}/circuit/${action}`)
  return data
}
