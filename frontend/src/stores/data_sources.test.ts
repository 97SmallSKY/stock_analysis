import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/data_sources', () => ({
  fetchStatus: vi.fn(),
  setPriority: vi.fn(),
  retryAdapter: vi.fn(),
  circuitAction: vi.fn(),
}))

import { useDataSourceStore } from './data_sources'
import {
  fetchStatus, setPriority, retryAdapter, circuitAction,
} from '@/api/data_sources'

const fetchStatusMock = fetchStatus as unknown as ReturnType<typeof vi.fn>
const setPriorityMock = setPriority as unknown as ReturnType<typeof vi.fn>
const retryAdapterMock = retryAdapter as unknown as ReturnType<typeof vi.fn>
const circuitActionMock = circuitAction as unknown as ReturnType<typeof vi.fn>

function makeStatus() {
  return {
    adapters: [
      {
        name: 'akshare',
        initialized: true,
        circuit: { state: 'closed', failure_count: 0, last_failure_at: null },
        last_health: { is_healthy: true, latency_ms: 120, checked_at: null, error: null },
      },
      {
        name: 'tushare',
        initialized: false,
        circuit: { state: 'open', failure_count: 3, last_failure_at: null },
        last_health: null,
      },
    ],
    priority_chains: {
      DAILY_KLINE: ['akshare', 'tushare'],
      LHB_STOCK: ['akshare'],
    },
  }
}

describe('useDataSourceStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    fetchStatusMock.mockReset()
    setPriorityMock.mockReset()
    retryAdapterMock.mockReset()
    circuitActionMock.mockReset()
  })

  it('load 写入 status', async () => {
    fetchStatusMock.mockResolvedValue(makeStatus())
    const store = useDataSourceStore()
    await store.load()
    expect(store.status?.adapters).toHaveLength(2)
    expect(store.status?.priority_chains.DAILY_KLINE).toEqual(['akshare', 'tushare'])
    expect(store.loading).toBe(false)
  })

  it('applyPriority 乐观更新本地 chain', async () => {
    fetchStatusMock.mockResolvedValue(makeStatus())
    setPriorityMock.mockResolvedValue({ ok: true })
    const store = useDataSourceStore()
    await store.load()
    await store.applyPriority('DAILY_KLINE', ['tushare', 'akshare'])
    expect(store.status?.priority_chains.DAILY_KLINE).toEqual(['tushare', 'akshare'])
    expect(setPriorityMock).toHaveBeenCalledWith('DAILY_KLINE', ['tushare', 'akshare'])
  })

  it('retry 更新 adapter.initialized', async () => {
    fetchStatusMock.mockResolvedValue(makeStatus())
    retryAdapterMock.mockResolvedValue({ ok: true, name: 'tushare', initialized: true })
    const store = useDataSourceStore()
    await store.load()
    await store.retry('tushare')
    const a = store.status?.adapters.find((x) => x.name === 'tushare')
    expect(a?.initialized).toBe(true)
  })

  it('forceCircuit 将返回的 circuit 写回本地', async () => {
    fetchStatusMock.mockResolvedValue(makeStatus())
    circuitActionMock.mockResolvedValue({
      ok: true, name: 'tushare', action: 'close',
      status: { state: 'closed', failure_count: 0, last_failure_at: null },
    })
    const store = useDataSourceStore()
    await store.load()
    await store.forceCircuit('tushare', 'close')
    const a = store.status?.adapters.find((x) => x.name === 'tushare')
    expect(a?.circuit.state).toBe('closed')
  })

  it('load 失败时 loading 恢复并抛出', async () => {
    fetchStatusMock.mockRejectedValueOnce(new Error('fail'))
    const store = useDataSourceStore()
    await expect(store.load()).rejects.toThrow('fail')
    expect(store.loading).toBe(false)
    expect(store.status).toBeNull()
  })
})
