// P18.2 递归格式化 score_detail (嵌套对象) 成多行字符串.
// P19.8 (2026-04-20) 扩展:提供中文标签 + 雷达图数据抽取,供 Signals 页面下钻使用。
//
// 后端 score_detail 结构:
// {
//   fund: { net_pct: 80, bs_ratio: 60, float_mv: 40, l_net_abs: 0, reason_rank: 50 },
//   hist: { avg_chg_3d: 50, avg_chg_5d: 50, total_count: 100, win_rate_5d: 50 },
//   tech: { day_pct: 100, last5_cum: 50, pos_to_high: 50, volume_ratio: 50, turnover_rate: 100 },
//   weights: { fund: 0.35, hist: 0.25, tech: 0.4 },
//   sentiment_coef: 1.0
// }

/**
 * 中文标签字典。键 = 后端字段名,值 = 界面展示的中文名。
 * 没命中的字段仍原样显示。
 */
export const SCORE_LABEL: Record<string, string> = {
  // 一级分类
  fund: '资金维度',
  hist: '历史表现',
  tech: '技术面',
  weights: '权重配比',
  sentiment_coef: '情绪系数',

  // fund 子项
  net_pct: '净买入占比',
  bs_ratio: '买卖比',
  float_mv: '流通市值',
  l_net_abs: '净买额',
  reason_rank: '上榜类型',

  // hist 子项
  avg_chg_3d: '近3日平均涨幅',
  avg_chg_5d: '近5日平均涨幅',
  total_count: '历史上榜次数',
  win_rate_5d: '5日胜率',

  // tech 子项
  day_pct: '当日涨幅',
  last5_cum: '近5日累计涨幅',
  pos_to_high: '距离高点',
  volume_ratio: '量比',
  turnover_rate: '换手率',
}

/** 翻译字段名。优先查字典,找不到返回原名。 */
export function labelOf(key: string): string {
  return SCORE_LABEL[key] ?? key
}

/**
 * 递归格式化为多行字符串(向后兼容,Signals 页的 Modal 显示仍用此函数)。
 * P19.8: **改用中文标签**,数值 <= 100 视为得分显示 "得分 X" 后缀提示。
 */
export function formatScoreDetail(
  d: Record<string, unknown> | null | undefined,
  indent = 0,
): string {
  if (d == null) return ''
  const pad = '  '.repeat(indent)
  const lines: string[] = []
  for (const [k, v] of Object.entries(d)) {
    const label = labelOf(k)
    if (v != null && typeof v === 'object' && !Array.isArray(v)) {
      lines.push(`${pad}${label}:`)
      lines.push(formatScoreDetail(v as Record<string, unknown>, indent + 1))
    } else if (Array.isArray(v)) {
      lines.push(`${pad}${label}: [${v.join(', ')}]`)
    } else if (typeof v === 'number') {
      // 浮点数保留 2 位, 整数原样
      const s = Number.isInteger(v) ? String(v) : v.toFixed(2)
      lines.push(`${pad}${label}: ${s}`)
    } else {
      lines.push(`${pad}${label}: ${v}`)
    }
  }
  return lines.join('\n')
}

/**
 * P19.8: 从 score_detail 提取用于**雷达图**的 (标签, 值) 对。
 *
 * 策略:只取每个一级分类(fund/hist/tech)下的数值子项,不展开 weights/coef。
 * 每个点的值 = 对应子项得分(后端已归一到 0~100)。
 *
 * 返回格式直接喂给 ECharts Radar:
 *   indicator: [{ name: '净买入占比', max: 100 }, ...]
 *   value:     [80, 60, ...]
 */
export interface RadarPoint {
  indicator: { name: string; max: number }[]
  value: number[]
  /** 分组索引(与 indicator 等长):0=fund 1=hist 2=tech,用于染色 */
  group: number[]
}

export function extractRadarData(
  d: Record<string, unknown> | null | undefined,
): RadarPoint {
  const pt: RadarPoint = { indicator: [], value: [], group: [] }
  if (d == null) return pt

  const buckets: { key: 'fund' | 'hist' | 'tech'; idx: number }[] = [
    { key: 'fund', idx: 0 },
    { key: 'hist', idx: 1 },
    { key: 'tech', idx: 2 },
  ]

  for (const { key, idx } of buckets) {
    const sub = d[key] as Record<string, unknown> | undefined
    if (!sub || typeof sub !== 'object') continue
    for (const [k, v] of Object.entries(sub)) {
      if (typeof v !== 'number') continue
      pt.indicator.push({ name: labelOf(k), max: 100 })
      // clamp 到 [0, 100],避免异常值撑破图
      pt.value.push(Math.max(0, Math.min(100, v)))
      pt.group.push(idx)
    }
  }
  return pt
}
