// P16.5 T+N 收益汇总纯函数
// 输入: LhbRow 列表 (需 with_tn=true 请求).
// 输出: t1/t5 的平均收益 + 胜率 + 有效样本数.

export interface TnRowLike {
  t1_pnl_pct?: number | null
  t5_pnl_pct?: number | null
  t1_limit_up?: boolean | null
  t5_high_pct?: number | null
}

export interface TnSummary {
  t1Cnt: number
  t1Avg: number | null
  t1WinRate: number | null // T+1 收益 > 0 的样本占比
  t1LimitUpCnt: number
  t5Cnt: number
  t5Avg: number | null
  t5WinRate: number | null
  t5HighAvg: number | null
}

export function tnSummarize(rows: TnRowLike[]): TnSummary {
  let t1Sum = 0, t1Cnt = 0, t1Win = 0, t1LU = 0
  let t5Sum = 0, t5Cnt = 0, t5Win = 0
  let t5HSum = 0, t5HCnt = 0
  for (const r of rows) {
    const v1 = r.t1_pnl_pct
    if (typeof v1 === 'number' && !Number.isNaN(v1)) {
      t1Cnt += 1; t1Sum += v1
      if (v1 > 0) t1Win += 1
    }
    if (r.t1_limit_up === true) t1LU += 1
    const v5 = r.t5_pnl_pct
    if (typeof v5 === 'number' && !Number.isNaN(v5)) {
      t5Cnt += 1; t5Sum += v5
      if (v5 > 0) t5Win += 1
    }
    const h5 = r.t5_high_pct
    if (typeof h5 === 'number' && !Number.isNaN(h5)) {
      t5HCnt += 1; t5HSum += h5
    }
  }
  return {
    t1Cnt,
    t1Avg: t1Cnt ? t1Sum / t1Cnt : null,
    t1WinRate: t1Cnt ? t1Win / t1Cnt : null,
    t1LimitUpCnt: t1LU,
    t5Cnt,
    t5Avg: t5Cnt ? t5Sum / t5Cnt : null,
    t5WinRate: t5Cnt ? t5Win / t5Cnt : null,
    t5HighAvg: t5HCnt ? t5HSum / t5HCnt : null,
  }
}
