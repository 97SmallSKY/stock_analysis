/**
 * P19.5 (2026-04-20): 日期范围快捷选择器的纯函数工具。
 *
 * 与 DateRangeQuickPick.vue 配合使用。组件里只做 UI,
 * 计算逻辑全集中在这里,方便单测。
 *
 * 约定(依据用户需求 2026-04-20):
 * - **快捷钮改的是「开始日期」,不改结束日期**。若用户没选结束,
 *   默认 = 今天。
 * - **默认范围 = 一年前 → 今天**(所有表格页)。
 * - 所有日期使用 `YYYY-MM-DD` 字符串(ISO 短形式),避免 Date 对象
 *   在跨时区场景下的歧义。
 */

export type QuickRange =
  | 'today'
  | '5d'
  | '1w'
  | '1m'
  | '3m'
  | '6m'   // K 线专用
  | 'q'
  | '1y'

export interface DateRange {
  start: string  // YYYY-MM-DD
  end: string    // YYYY-MM-DD
}

/** 今日 ISO 短日期(YYYY-MM-DD),**本地时区**。 */
export function today(now: Date = new Date()): string {
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

/** 把 YYYY-MM-DD 往前推 n 天(负数表示往后推),返回新 ISO 日期。 */
export function addDays(iso: string, n: number): string {
  const d = new Date(iso + 'T00:00:00')
  d.setDate(d.getDate() + n)
  return today(d)
}

/** 把 YYYY-MM-DD 往前推 n 个月(按日历月,2-29 转 2-28 自动处理)。 */
export function addMonths(iso: string, n: number): string {
  const d = new Date(iso + 'T00:00:00')
  d.setMonth(d.getMonth() + n)
  return today(d)
}

/**
 * 计算快捷范围的 [start, end]。
 * end 默认为今天,可传入 `anchorEnd` 覆盖(通常 = 用户已选的结束日期)。
 *
 * 注意:'today' 是「只看今天一天」,start = end = 今天。
 * '5d' 是过去 5 天(含今天),start = end - 4d。
 * '1w' = 过去 7 天,'1m' = 过去 1 个自然月,依此类推。
 */
export function calcRange(q: QuickRange, anchorEnd?: string): DateRange {
  const end = anchorEnd ?? today()
  let start: string
  switch (q) {
    case 'today':
      start = end
      break
    case '5d':
      start = addDays(end, -4)  // 含今天共 5 天
      break
    case '1w':
      start = addDays(end, -6)  // 含今天共 7 天
      break
    case '1m':
      start = addMonths(end, -1)
      break
    case '3m':
      start = addMonths(end, -3)
      break
    case '6m':
      start = addMonths(end, -6)
      break
    case 'q':
      // 一个季度 = 3 个月(不按自然季度切分,避免跨季度时的困惑)
      start = addMonths(end, -3)
      break
    case '1y':
      start = addMonths(end, -12)
      break
    default:
      // exhaustive
      start = end
  }
  return { start, end }
}

/** 所有可选快捷钮(供表格页使用,K 线页按需挑选)。 */
export const DEFAULT_QUICK_OPTIONS: { key: QuickRange; label: string }[] = [
  { key: '1y', label: '1年' },
  { key: 'q', label: '季度' },
  { key: '6m', label: '6个月' },
  { key: '3m', label: '3个月' },
  { key: '1m', label: '1个月' },
  { key: '1w', label: '1周' },
  { key: '5d', label: '5日' },
  { key: 'today', label: '今天' },
]

/** 默认范围 = 一年前 → 今天(用户需求:表格页的默认)。 */
export function defaultRange(): DateRange {
  return calcRange('1y')
}

/**
 * 把 ISO 日期(YYYY-MM-DD)转成后端接口期望的紧凑格式 YYYYMMDD。
 * 全系后端的 `from` / `to` 查询参数都是这种格式,保持一致。
 */
export function toYmd(iso: string): string {
  return iso.replace(/-/g, '')
}

/** 反向:YYYYMMDD → YYYY-MM-DD。非法格式返回空字符串。 */
export function fromYmd(ymd: string): string {
  if (!ymd || ymd.length !== 8) return ''
  return `${ymd.slice(0, 4)}-${ymd.slice(4, 6)}-${ymd.slice(6, 8)}`
}
