import http from './http'

// STO-82: 四大指数实时行情。后端返回 {items: [...]}。
export interface IndexSnapshotItem {
  code: string
  name: string
  latest: number
  change: number
  pct: number
}

export interface IndexSnapshotResp {
  items: IndexSnapshotItem[]
  error?: string
}

export async function fetchIndexSnapshot(): Promise<IndexSnapshotResp> {
  const { data } = await http.get<IndexSnapshotResp>('/api/index/snapshot', { timeout: 20_000 })
  return data
}
