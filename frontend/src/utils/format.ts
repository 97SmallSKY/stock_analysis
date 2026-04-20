// 统一的轻量格式化函数, 覆盖 Dashboard / Lhb 列表常用场景

export function fmtAmount(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '-'
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return v.toFixed(2)
}

/**
 * 金额单位是「万元」时的格式化 (LHB buy_amount/sell_amount/net_amount)。
 * 输入: 万元数值;>= 1e4 万 = 1 亿,转 `x.xx 亿`;否则保留 `x.xx 万`。
 * 绝对小于 0.01 万(= 100 元)时视为 0。
 */
export function fmtAmountWan(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '-'
  const abs = Math.abs(v)
  if (abs >= 1e4) return `${(v / 1e4).toFixed(2)} 亿`
  return `${v.toFixed(2)} 万`
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '-'
  return `${v.toFixed(digits)}%`
}

/**
 * STO-30: 股票价格自适应精度格式化。
 *
 * 规则(以区间最大价 maxVal 决定整档小数位,不按每个点单独判断,
 * 避免 Y 轴刻度精度跳动):
 *   maxVal < 10    → 3 位小数(低价股/北交所/ST 股,如 1.360)
 *   10 <= max < 100 → 2 位小数(主流价位,如 12.35)
 *   maxVal >= 100  → 1 位小数(高价股,如 158.7)
 *
 * 用于 K 线 Y 轴 axisLabel / markPoint label / tooltip formatter。
 * NaN / null / undefined 返回 '-'(与其他 fmtXxx 一致)。
 */
export function formatPrice(
  val: number | null | undefined,
  maxVal: number,
): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '-'
  const mv = Math.abs(maxVal)
  const digits = mv < 10 ? 3 : mv < 100 ? 2 : 1
  return val.toFixed(digits)
}

export function fmtNumber(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '-'
  return String(v)
}

export function fmtDate(v: string | null | undefined): string {
  if (!v) return '-'
  // 后端已 ISO-8601, 截取 YYYY-MM-DD
  return v.length >= 10 ? v.slice(0, 10) : v
}

// Dashboard 适配器健康灯: ok=绿, false=红, null=灰 (未探针)
export function healthColor(ok: boolean | null | undefined): string {
  if (ok === true) return '#52c41a'
  if (ok === false) return '#f5222d'
  return '#d9d9d9'
}

// 龙虎榜席位排位 1/2/3 徽章样式
export function rankClass(r: number | null | undefined): string {
  if (r == null) return ''
  if (r === 1) return 'rank-1'
  if (r === 2) return 'rank-2'
  if (r === 3) return 'rank-3'
  return ''
}

// Circuit breaker 状态标签色
export function cbColor(state: string | null | undefined): string {
  switch (state) {
    case 'closed': return 'green'
    case 'half_open': return 'gold'
    case 'open': return 'red'
    default: return 'default'
  }
}
