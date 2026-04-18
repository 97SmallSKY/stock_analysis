import { onBeforeUnmount, ref, watch } from 'vue'

// W8-4: 前端订阅盘中快照
//
// 用法:
//   const codes = ref(['600000.SH'])
//   const { last, connected, send } = useSnapshot(codes)
//
//   codes.value = ['000001.SZ']  // 自动发 set_codes 变更订阅, 无需重连
//   watch(last, row => { /* 每 10s 拿到最新一行 */ })

export interface SnapshotRow {
  ts_code: string
  ts?: string
  price?: number | null
  pct_change?: number | null
  change?: number | null
  volume?: number | null
  amount?: number | null
  [k: string]: unknown
}

export interface UseSnapshotOptions {
  url?: string
  /** 断线自动重连 (毫秒). 0 表示不重连. 默认 3000 */
  reconnectDelayMs?: number
}

function wsUrlFromLocation(path = '/ws/snapshot'): string {
  if (typeof window === 'undefined') return `ws://localhost:8000${path}`
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}${path}`
}

export function useSnapshot(
  codesRef: { value: string[] },
  opts: UseSnapshotOptions = {},
) {
  const delay = opts.reconnectDelayMs ?? 3000
  const baseUrl = opts.url ?? wsUrlFromLocation()

  const last = ref<SnapshotRow | null>(null)
  const byCode = ref<Record<string, SnapshotRow>>({})
  const connected = ref(false)
  const error = ref<string | null>(null)

  let ws: WebSocket | null = null
  let closedByUser = false
  let retryTimer: number | null = null

  function currentCodesParam(): string {
    return (codesRef.value || []).join(',')
  }

  function connect() {
    try {
      const cs = currentCodesParam()
      const u = cs ? `${baseUrl}?codes=${encodeURIComponent(cs)}` : baseUrl
      ws = new WebSocket(u)
    } catch (e) {
      error.value = String((e as Error)?.message ?? e)
      scheduleReconnect()
      return
    }
    ws.onopen = () => {
      connected.value = true
      error.value = null
    }
    ws.onmessage = (ev) => {
      let row: SnapshotRow | null = null
      try {
        row = JSON.parse(ev.data)
      } catch {
        return
      }
      if (!row || typeof row !== 'object') return
      // 服务端 op=hello/ack 不算 snapshot
      if ((row as any).op) return
      if (typeof row.ts_code !== 'string') return
      last.value = row
      byCode.value = { ...byCode.value, [row.ts_code]: row }
    }
    ws.onerror = (ev) => {
      error.value = 'websocket error'
    }
    ws.onclose = () => {
      connected.value = false
      ws = null
      if (!closedByUser) scheduleReconnect()
    }
  }

  function scheduleReconnect() {
    if (delay <= 0 || closedByUser) return
    if (retryTimer !== null) return
    retryTimer = window.setTimeout(() => {
      retryTimer = null
      connect()
    }, delay)
  }

  function send(obj: unknown) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj))
    }
  }

  function close() {
    closedByUser = true
    if (retryTimer !== null) {
      window.clearTimeout(retryTimer)
      retryTimer = null
    }
    if (ws) {
      ws.close()
      ws = null
    }
    connected.value = false
  }

  // codes 变化 -> 如果已连接就发 set_codes, 否则等 onopen 带上新 query
  watch(() => [...(codesRef.value || [])], (next) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      send({ op: 'set_codes', codes: next })
    }
  })

  connect()
  onBeforeUnmount(close)

  return { last, byCode, connected, error, send, close }
}
