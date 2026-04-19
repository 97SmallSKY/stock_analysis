import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('./http', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import http from './http'
import {
  listRobots, pauseRobot, resumeRobot, enableRobot, disableRobot,
  createRobot, getRobotStatus,
} from './robots'

const getMock = http.get as unknown as ReturnType<typeof vi.fn>
const postMock = http.post as unknown as ReturnType<typeof vi.fn>

beforeEach(() => { getMock.mockReset(); postMock.mockReset() })

describe('listRobots / getRobotStatus', () => {
  it('listRobots 命中 GET /api/robots', async () => {
    getMock.mockResolvedValue({ data: { robots: [] } })
    const r = await listRobots()
    expect(getMock.mock.calls[0][0]).toBe('/api/robots')
    expect(r.robots).toEqual([])
  })
  it('getRobotStatus 拼 id', async () => {
    getMock.mockResolvedValue({ data: { ok: true, snapshot: { robot_id: 7 } } })
    await getRobotStatus(7)
    expect(getMock.mock.calls[0][0]).toBe('/api/robots/7/status')
  })
})

describe('动作 post 端点', () => {
  beforeEach(() => {
    postMock.mockResolvedValue({ data: { ok: true, snapshot: {} } })
  })
  it('pause/resume/enable/disable 分别命中各自 URL', async () => {
    await pauseRobot(1); await resumeRobot(1); await enableRobot(1); await disableRobot(1)
    const urls = postMock.mock.calls.map((c) => c[0])
    expect(urls).toEqual([
      '/api/robots/1/pause',
      '/api/robots/1/resume',
      '/api/robots/1/enable',
      '/api/robots/1/disable',
    ])
  })
})

describe('createRobot', () => {
  it('POST /api/robots/create, 透传 body', async () => {
    postMock.mockResolvedValue({
      data: { ok: true, snapshot: { robot_id: 42, state: 'IDLE', cash: 1000, enabled: false, position_count: 0, total_assets: 1000, daily_pnl_pct: 0, positions: [] } },
    })
    const r = await createRobot({ robot_id: 42, cash: 1000, enabled: false })
    const [url, body] = postMock.mock.calls[0]
    expect(url).toBe('/api/robots/create')
    expect(body).toMatchObject({ robot_id: 42, cash: 1000, enabled: false })
    expect(r.snapshot.robot_id).toBe(42)
  })

  it('robot_id 缺省不传', async () => {
    postMock.mockResolvedValue({
      data: { ok: true, snapshot: { robot_id: 1, state: 'IDLE', cash: 500000, enabled: false, position_count: 0, total_assets: 500000, daily_pnl_pct: 0, positions: [] } },
    })
    await createRobot({ cash: 500000 })
    const body = postMock.mock.calls[0][1]
    expect(body.robot_id).toBeUndefined()
    expect(body.cash).toBe(500000)
  })
})
