// P16.2 LhbPnl 聚合纯函数
// 按「上榜原因」和「股票」两个维度聚合,供 LhbPnl.vue 使用。
// 三柱图固定三色:净买入 #d4380d,上榜次数 #fa8c16,平均评分 #722ed1。

export interface LhbRowLike {
  ts_code: string
  name?: string | null
  reason?: string | null
  buy_amount?: number | null
  sell_amount?: number | null
  net_amount?: number | null
  score?: number | null
}

export interface ReasonAgg {
  reason: string
  net_sum: number
  count: number
  avg_score: number | null
  avg_net: number // 平均每次上榜净买入
}

export interface StockAgg {
  ts_code: string
  name: string | null
  net_sum: number
  buy_sum: number
  sell_sum: number
  count: number
  avg_score: number | null
  avg_net: number
}

export const PNL_COLORS = {
  netPos: '#d4380d',
  netNeg: '#389e0d',
  count: '#fa8c16',
  score: '#722ed1',
} as const

export function aggregateByReason(rows: LhbRowLike[], topN = 10): ReasonAgg[] {
  const m = new Map<string, { net: number; cnt: number; scores: number[] }>()
  for (const r of rows) {
    const k = (r.reason ?? '').trim() || '(未知)'
    const cur = m.get(k) ?? { net: 0, cnt: 0, scores: [] }
    cur.net += r.net_amount ?? 0
    cur.cnt += 1
    if (r.score != null) cur.scores.push(r.score)
    m.set(k, cur)
  }
  const arr: ReasonAgg[] = [...m.entries()].map(([reason, v]) => ({
    reason,
    net_sum: v.net,
    count: v.cnt,
    avg_score: v.scores.length ? v.scores.reduce((a, b) => a + b, 0) / v.scores.length : null,
    avg_net: v.cnt ? v.net / v.cnt : 0,
  }))
  arr.sort((a, b) => Math.abs(b.net_sum) - Math.abs(a.net_sum))
  return arr.slice(0, topN)
}

export function aggregateByStock(rows: LhbRowLike[]): StockAgg[] {
  const m = new Map<
    string,
    { name: string | null; buy: number; sell: number; net: number; cnt: number; scores: number[] }
  >()
  for (const r of rows) {
    const cur = m.get(r.ts_code) ?? { name: null, buy: 0, sell: 0, net: 0, cnt: 0, scores: [] }
    if (!cur.name && r.name) cur.name = r.name
    cur.buy += r.buy_amount ?? 0
    cur.sell += r.sell_amount ?? 0
    cur.net += r.net_amount ?? 0
    cur.cnt += 1
    if (r.score != null) cur.scores.push(r.score)
    m.set(r.ts_code, cur)
  }
  return [...m.entries()].map(([ts_code, v]) => ({
    ts_code,
    name: v.name,
    buy_sum: v.buy,
    sell_sum: v.sell,
    net_sum: v.net,
    count: v.cnt,
    avg_score: v.scores.length ? v.scores.reduce((a, b) => a + b, 0) / v.scores.length : null,
    avg_net: v.cnt ? v.net / v.cnt : 0,
  }))
}

export function summarize(rows: LhbRowLike[]) {
  let buy = 0, sell = 0, net = 0
  let scoreSum = 0, scoreCnt = 0
  for (const r of rows) {
    buy += r.buy_amount ?? 0
    sell += r.sell_amount ?? 0
    net += r.net_amount ?? 0
    if (r.score != null) { scoreSum += r.score; scoreCnt += 1 }
  }
  return {
    count: rows.length,
    buy_sum: buy,
    sell_sum: sell,
    net_sum: net,
    avg_score: scoreCnt ? scoreSum / scoreCnt : null,
  }
}
