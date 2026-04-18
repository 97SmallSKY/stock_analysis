import { describe, it, expect, beforeEach, vi } from 'vitest'
import { ref, nextTick } from 'vue'

// 我们不想触发真实的 onBeforeUnmount 警告, 用 withSetup 包一层.
import { useSnapshot } from './useSnapshot'

class FakeWebSocket {
  static OPEN = 1
  static instances: FakeWebSocket[] = []
  public readyState = 0
  public url: string
  public onopen: ((ev?: unknown) => void) | null = null
  public onmessage: ((ev: { data: string }) => void) | null = null
  public onclose: ((ev?: unknown) => void) | null = null
  public onerror: ((ev?: unknown) => void) | null = null
  public sent: string[] = []
  public closed = false
  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }
  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }
  emit(data: unknown) {
    this.onmessage?.({ data: typeof data === 'string' ? data : JSON.stringify(data) })
  }
  send(s: string) { this.sent.push(s) }
  close() { this.closed = true; this.readyState = 3; this.onclose?.() }
}

function withSetup<T>(fn: () => T): { result: T } {
  // 最简替代 Vue 测试套件的 setup wrapper: 组件 mount 以支持 onBeforeUnmount
  // 这里不挂组件, 仅接受 onBeforeUnmount 警告. 函数在 vitest 下能正常运行.
  const result = fn()
  return { result }
}

describe('useSnapshot', () => {
  const OriginalWS = globalThis.WebSocket

  beforeEach(() => {
    FakeWebSocket.instances = []
    // @ts-expect-error override
    globalThis.WebSocket = FakeWebSocket
  })

  afterEachRestore()

  function afterEachRestore() {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    return globalThis.addEventListener?.('beforeunload', () => {
      // no-op
    })
  }

  it('连接时 URL 带 codes query', () => {
    const codes = ref(['A.SZ', 'B.SZ'])
    const { result: api } = withSetup(() =>
      useSnapshot(codes, { url: 'ws://test/ws/snapshot', reconnectDelayMs: 0 }),
    )
    expect(FakeWebSocket.instances.length).toBe(1)
    expect(FakeWebSocket.instances[0].url).toContain('ws://test/ws/snapshot?codes=')
    expect(FakeWebSocket.instances[0].url).toContain('A.SZ')
    expect(FakeWebSocket.instances[0].url).toContain('B.SZ')
    api.close()
  })

  it('收到 snapshot 写入 last / byCode, 忽略 op=hello', () => {
    const codes = ref(['A.SZ'])
    const { result: api } = withSetup(() =>
      useSnapshot(codes, { url: 'ws://t', reconnectDelayMs: 0 }),
    )
    const ws = FakeWebSocket.instances[0]
    ws.open()
    expect(api.connected.value).toBe(true)
    // hello 不算快照
    ws.emit({ op: 'hello', codes: ['A.SZ'] })
    expect(api.last.value).toBeNull()
    // 真快照
    ws.emit({ ts_code: 'A.SZ', price: 10.25 })
    expect(api.last.value?.ts_code).toBe('A.SZ')
    expect(api.byCode.value['A.SZ']?.price).toBe(10.25)
    api.close()
  })

  it('codes 变化后, 已连接时发 set_codes', async () => {
    const codes = ref(['A.SZ'])
    const { result: api } = withSetup(() =>
      useSnapshot(codes, { url: 'ws://t', reconnectDelayMs: 0 }),
    )
    const ws = FakeWebSocket.instances[0]
    ws.open()
    codes.value = ['X.SH']
    await nextTick()
    expect(ws.sent.length).toBe(1)
    const msg = JSON.parse(ws.sent[0])
    expect(msg).toEqual({ op: 'set_codes', codes: ['X.SH'] })
    api.close()
  })

  it('close 阻止重连 timer', () => {
    vi.useFakeTimers()
    const codes = ref<string[]>([])
    const { result: api } = withSetup(() =>
      useSnapshot(codes, { url: 'ws://t', reconnectDelayMs: 1000 }),
    )
    const ws = FakeWebSocket.instances[0]
    api.close()
    // 模拟底层触发 close, 也不应重连
    ws.onclose?.()
    vi.advanceTimersByTime(5000)
    expect(FakeWebSocket.instances.length).toBe(1)
    vi.useRealTimers()
  })

  it('ev.data 非 JSON 时忽略', () => {
    const codes = ref(['A.SZ'])
    const { result: api } = withSetup(() =>
      useSnapshot(codes, { url: 'ws://t', reconnectDelayMs: 0 }),
    )
    const ws = FakeWebSocket.instances[0]
    ws.open()
    ws.emit('garbage-not-json')
    expect(api.last.value).toBeNull()
    api.close()
  })

  afterAllRestore()
  function afterAllRestore() {
    ;(globalThis as any).WebSocket = OriginalWS
  }
})
