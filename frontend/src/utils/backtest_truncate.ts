// STO-90 / STO-80c: K 线买卖点截断(无未来函数)
//
// 语义:在 t 时刻的「当前视角」,只能看到 t-1 之前(不含 t-1 当天及之后)的
// 买卖点。用于回放 / 分步 review 时防止展示仍未发生的交易。
//
// 示例:
//   points = [{date:'2026-01-03',...}, {date:'2026-01-05',...}]
//   currentDate = '2026-01-05'
//   → 只返回 01-03 这一条(01-05 当天视角里,01-05 尚未「完成」)

export interface TradePoint {
  date: string            // YYYY-MM-DD
  side: 'BUY' | 'SELL'
  price: number
  ts_code?: string
  quantity?: number
}

/**
 * 保留严格早于 currentDate 的买卖点。
 *
 * - currentDate 为空串或 undefined → 返回空数组(视角未定,啥都不给看)
 * - points 为空 → 空数组
 * - 日期比较用 lexicographic,ISO 格式保证正确
 */
export function truncateTradesBeforeDate(
  points: TradePoint[],
  currentDate: string | undefined | null,
): TradePoint[] {
  if (!currentDate || !points || points.length === 0) return []
  return points.filter((p) => p.date < currentDate)
}
