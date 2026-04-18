import { describe, it, expect, beforeEach } from 'vitest'
import { useStockDrill } from './useStockDrill'

describe('useStockDrill', () => {
  beforeEach(() => {
    const { close, state } = useStockDrill()
    close()
    state.tsCode = null
  })

  it('open(ts_code) 置位 state.open + ts_code', () => {
    const drill = useStockDrill()
    expect(drill.state.open).toBe(false)
    drill.open('000001.SZ')
    expect(drill.state.open).toBe(true)
    expect(drill.state.tsCode).toBe('000001.SZ')
  })

  it('close() 只关, 不清 ts_code (便于再次打开)', () => {
    const drill = useStockDrill()
    drill.open('600000.SH')
    drill.close()
    expect(drill.state.open).toBe(false)
    expect(drill.state.tsCode).toBe('600000.SH')
  })

  it('多次调用 useStockDrill() 是同一 state (单例)', () => {
    const a = useStockDrill()
    const b = useStockDrill()
    a.open('000002.SZ')
    expect(b.state.open).toBe(true)
    expect(b.state.tsCode).toBe('000002.SZ')
  })
})
