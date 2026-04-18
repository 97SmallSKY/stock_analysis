import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// 必须在 import store 之前完成 mock (vitest 自动 hoist vi.mock)
vi.mock('@/api/dashboard', () => ({
  fetchDashboard: vi.fn(),
}))

import { useDashboardStore } from './dashboard'
import { fetchDashboard } from '@/api/dashboard'

const fetchDashboardMock = fetchDashboard as unknown as ReturnType<typeof vi.fn>

describe('useDashboardStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    fetchDashboardMock.mockReset()
  })

  it('load 成功写入 data 并切换 loading', async () => {
    const payload = {
      sentiment: null,
      top_lhb: [
        { ts_code: '000001.SZ', trade_date: '2025-01-10', reason: null, net_amount: 1e8, score: 80 },
      ],
      adapters: [],
    }
    fetchDashboardMock.mockResolvedValueOnce(payload)
    const store = useDashboardStore()
    expect(store.loading).toBe(false)
    const p = store.load(10)
    expect(store.loading).toBe(true)
    await p
    expect(store.loading).toBe(false)
    expect(store.data).toEqual(payload)
    expect(store.error).toBeNull()
    expect(fetchDashboardMock).toHaveBeenCalledWith(10)
  })

  it('并发 load 共享 inflight (去重)', async () => {
    let resolve!: (v: any) => void
    fetchDashboardMock.mockReturnValueOnce(new Promise((r) => { resolve = r }))
    const store = useDashboardStore()
    const a = store.load(5)
    const b = store.load(5) // 非 force, 应复用 inflight
    expect(fetchDashboardMock).toHaveBeenCalledTimes(1)
    resolve({ sentiment: null, top_lhb: [], adapters: [] })
    await Promise.all([a, b])
    expect(store.data).toEqual({ sentiment: null, top_lhb: [], adapters: [] })
  })

  it('force=true 强制重新拉取', async () => {
    fetchDashboardMock.mockResolvedValue({ sentiment: null, top_lhb: [], adapters: [] })
    const store = useDashboardStore()
    await store.load(10)
    await store.load(10, true)
    expect(fetchDashboardMock).toHaveBeenCalledTimes(2)
  })

  it('load 失败时 error 非空并抛出', async () => {
    fetchDashboardMock.mockRejectedValueOnce(new Error('boom'))
    const store = useDashboardStore()
    await expect(store.load(10)).rejects.toThrow('boom')
    expect(store.error).toContain('boom')
    expect(store.loading).toBe(false)
  })
})
