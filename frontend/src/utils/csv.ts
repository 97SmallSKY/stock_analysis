// 轻量 CSV 导出, 覆盖前端 /backtest /reports 的 runs/明细导出.
// 不引入 xlsx 库 (避免膨胀), 浏览器自带下载足够.

export function rowsToCsv<T extends Record<string, unknown>>(
  rows: T[],
  columns?: string[],
): string {
  if (!rows.length) return ''
  const cols = columns && columns.length
    ? columns
    : Object.keys(rows[0])
  const esc = (v: unknown): string => {
    if (v === null || v === undefined) return ''
    const s = typeof v === 'string' ? v : String(v)
    // RFC 4180: 含 , " \n 时整段用双引号包, 内部双引号翻倍
    if (/[",\n\r]/.test(s)) {
      return `"${s.replace(/"/g, '""')}"`
    }
    return s
  }
  const head = cols.map(esc).join(',')
  const body = rows
    .map((r) => cols.map((c) => esc(r[c])).join(','))
    .join('\n')
  return `${head}\n${body}`
}

export function downloadCsv(
  filename: string, csv: string,
): void {
  if (typeof window === 'undefined') return
  // 加 UTF-8 BOM, Excel 打开中文不乱码
  const blob = new Blob(['\uFEFF', csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
