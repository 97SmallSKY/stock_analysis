// Signals 页多维度聚合纯函数 — 便于独立单测

export interface SignalLike {
  ts_code: string
  trade_date: string | null
  reason: string | null
  net_amount: number | null
  score: number | null
}

export interface ScoreBucket { label: string; min: number; max: number; count: number }
export interface NetBucket { label: string; count: number }
export interface DailyBucket { date: string; count: number }

/** 评分段分布: 0-50 / 50-70 / 70-85 / 85-100 四段 */
export function scoreBuckets(rows: SignalLike[]): ScoreBucket[] {
  const defs: ScoreBucket[] = [
    { label: '0-50', min: 0, max: 50, count: 0 },
    { label: '50-70', min: 50, max: 70, count: 0 },
    { label: '70-85', min: 70, max: 85, count: 0 },
    { label: '85-100', min: 85, max: 100.01, count: 0 },
  ]
  for (const r of rows) {
    if (r.score == null) continue
    const b = defs.find((d) => r.score! >= d.min && r.score! < d.max)
    if (b) b.count += 1
  }
  return defs
}

/** 净买入区间分布 (LHB net_amount 单位是「万元」): <=-1亿 / -1亿~0 / 0~1亿 / 1亿~5亿 / >5亿 */
export function netBuckets(rows: SignalLike[]): NetBucket[] {
  const defs: NetBucket[] = [
    { label: '≤ -1亿', count: 0 },
    { label: '-1亿~0', count: 0 },
    { label: '0~1亿', count: 0 },
    { label: '1亿~5亿', count: 0 },
    { label: '> 5亿', count: 0 },
  ]
  for (const r of rows) {
    // 1 亿 = 10000 万
    const v = (r.net_amount ?? 0) / 1e4
    if (v <= -1) defs[0].count += 1
    else if (v < 0) defs[1].count += 1
    else if (v < 1) defs[2].count += 1
    else if (v < 5) defs[3].count += 1
    else defs[4].count += 1
  }
  return defs
}

/** 按日统计信号数量,按日期升序 */
export function dailyBuckets(rows: SignalLike[]): DailyBucket[] {
  const m = new Map<string, number>()
  for (const r of rows) {
    if (!r.trade_date) continue
    const d = r.trade_date.slice(0, 10)
    m.set(d, (m.get(d) ?? 0) + 1)
  }
  return [...m.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, count]) => ({ date, count }))
}

/** 汇总指标(count / avgScore / netSum / netAvg / topReasons) */
export interface Summary {
  count: number
  avgScore: number | null
  netSum: number
  netAvg: number | null
  topReasons: { reason: string; count: number }[]
  uniqStocks: number
}

export function summarize(rows: SignalLike[], topN = 5): Summary {
  const count = rows.length
  const scores = rows.map((r) => r.score).filter((v): v is number => v != null)
  const avgScore = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null
  const netSum = rows.reduce((a, r) => a + (r.net_amount ?? 0), 0)
  const netAvg = count ? netSum / count : null
  const reasonCnt = new Map<string, number>()
  for (const r of rows) {
    const k = (r.reason ?? '').trim() || '(未知)'
    reasonCnt.set(k, (reasonCnt.get(k) ?? 0) + 1)
  }
  const topReasons = [...reasonCnt.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, topN)
    .map(([reason, c]) => ({ reason, count: c }))
  const uniqStocks = new Set(rows.map((r) => r.ts_code)).size
  return { count, avgScore, netSum, netAvg, topReasons, uniqStocks }
}
