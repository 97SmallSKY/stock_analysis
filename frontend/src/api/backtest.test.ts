import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('./http', () => ({
  default: { post: vi.fn(), get: vi.fn() },
}))

import http from './http'
import { compareBacktests, COMPARE_TIMEOUT_MS } from './backtest'

const postMock = http.post as unknown as ReturnType<typeof vi.fn>

beforeEach(() => { postMock.mockReset() })

describe('compareBacktests', () => {
  it('STO-46: 传递 timeout 覆盖全局 10s,避免多策略对比超时', async () => {
    postMock.mockResolvedValue({
      data: { start_date: '20260101', end_date: '20260131', initial_capital: 1e6, runs: [], winner: null },
    })
    await compareBacktests({
      start_date: '20260101',
      end_date: '20260131',
      strategies: [{ name: 'a#1', strategy_name: 'a' }],
    })
    const [url, , cfg] = postMock.mock.calls[0]
    expect(url).toBe('/api/backtest/compare')
    expect(cfg).toMatchObject({ timeout: COMPARE_TIMEOUT_MS })
    expect(COMPARE_TIMEOUT_MS).toBeGreaterThan(10_000)
  })
})
