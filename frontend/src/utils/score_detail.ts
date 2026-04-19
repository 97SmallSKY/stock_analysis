// P18.2 递归格式化 score_detail (嵌套对象) 成多行字符串.
// 后端 score_detail 结构:
// {
//   fund: { net_pct: 80, bs_ratio: 60, float_mv: 40, l_net_abs: 0, reason_rank: 50 },
//   hist: { avg_chg_3d: 50, avg_chg_5d: 50, total_count: 100, win_rate_5d: 50 },
//   tech: { day_pct: 100, last5_cum: 50, pos_to_high: 50, volume_ratio: 50, turnover_rate: 100 },
//   weights: { fund: 0.35, hist: 0.25, tech: 0.4 },
//   sentiment_coef: 1.0
// }

export function formatScoreDetail(
  d: Record<string, unknown> | null | undefined,
  indent = 0,
): string {
  if (d == null) return ''
  const pad = '  '.repeat(indent)
  const lines: string[] = []
  for (const [k, v] of Object.entries(d)) {
    if (v != null && typeof v === 'object' && !Array.isArray(v)) {
      lines.push(`${pad}${k}:`)
      lines.push(formatScoreDetail(v as Record<string, unknown>, indent + 1))
    } else if (Array.isArray(v)) {
      lines.push(`${pad}${k}: [${v.join(', ')}]`)
    } else if (typeof v === 'number') {
      // 浮点数保留 2 位, 整数原样
      const s = Number.isInteger(v) ? String(v) : v.toFixed(2)
      lines.push(`${pad}${k}: ${s}`)
    } else {
      lines.push(`${pad}${k}: ${v}`)
    }
  }
  return lines.join('\n')
}
