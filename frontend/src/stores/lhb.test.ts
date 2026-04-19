import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/lhb', () => ({
  fetchLhb: vi.fn(),
  fetchLhbSeats: vi.fn(),
}))

import { useLhbStore } from './lhb'
import { fetchLhb, fetchLhbSeats } from '@/api/lhb'

const fetchLhbMock = fetchLhb as unknown as ReturnType<typeof vi.fn>
const fetchLhbSeatsMock = fetchLhbSeats as unknown as ReturnType<typeof vi.fn>

const emptyResp = { total: 0, limit: 50, offset: 0, rows: [] }

describe('useLhbStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    fetchLhbMock.mockReset()
    fetchLhbSeatsMock.mockReset()
  })

  it('初始 filter 默认 order_by=trade_date, desc, limit=50', () => {
    const store = useLhbStore()
    expect(store.filter.order_by).toBe('trade_date')
    expect(store.filter.order_desc).toBe(true)
    expect(store.filter.limit).toBe(50)
    expect(store.filter.offset).toBe(0)
  })

  it('search 合并 partial + 重置 offset', async () => {
    fetchLhbMock.mockResolvedValue({ ...emptyResp, total: 3, rows: [{ ts_code: 'X', trade_date: null }] as any })
    const store = useLhbStore()
    store.filter.offset = 200
    await store.search({ ts_code: '000001.SZ', score_min: 60 })
    const args = fetchLhbMock.mock.calls[0][0]
    expect(args.ts_code).toBe('000001.SZ')
    expect(args.score_min).toBe(60)
    expect(args.offset).toBe(0) // 重置
    expect(store.list.total).toBe(3)
  })

  it('page 不重置 offset, 保留其他 filter', async () => {
    fetchLhbMock.mockResolvedValue(emptyResp)
    const store = useLhbStore()
    await store.search({ ts_code: 'X' })
    fetchLhbMock.mockClear()
    fetchLhbMock.mockResolvedValue(emptyResp)
    await store.page(100, 50)
    const args = fetchLhbMock.mock.calls[0][0]
    expect(args.offset).toBe(100)
    expect(args.limit).toBe(50)
    expect(args.ts_code).toBe('X')
  })

  it('search 失败 loading 恢复并抛出', async () => {
    fetchLhbMock.mockRejectedValueOnce(new Error('500'))
    const store = useLhbStore()
    await expect(store.search()).rejects.toThrow('500')
    expect(store.loading).toBe(false)
  })

  it('loadSeats 写入 seats, 切换 seatsLoading', async () => {
    // P10.1: 后端 seats 字段对齐到 broker_name / rank_no / name / BUY
    fetchLhbSeatsMock.mockResolvedValue({
      rows: [
        {
          ts_code: 'X', name: '测试股份', trade_date: '2025-01-10',
          direction: 'BUY', broker_name: 'S1',
          buy_amount: 1, sell_amount: 0, net_amount: 1, rank_no: 1,
        },
      ],
    })
    const store = useLhbStore()
    await store.loadSeats({ trade_date: '2025-01-10', ts_code: 'X' })
    expect(store.seats).toHaveLength(1)
    expect(store.seats[0].broker_name).toBe('S1')
    expect(store.seats[0].rank_no).toBe(1)
    expect(store.seats[0].name).toBe('测试股份')
    expect(store.seatsLoading).toBe(false)
  })
})
