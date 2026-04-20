/**
 * K 线 Modal 全局单例 store。
 *
 * ⚠️ 整个调用链约定见 `docs/frontend/kline-modal-contract.md` (STO-41)
 *    —— rangeKey 枚举 / anchorDate 传递链 / 参数边界 / 空态状态机等,
 *    改之前**务必**读一遍,防止踩 P19.12 / P19.13 事故类型的坑。
 */
import { reactive } from 'vue'

// 单例 store: 全局只挂一个 StockDrillModal, 任何组件通过 open(ts_code, anchor_date?) 打开
// P19.12 (2026-04-20): 新增 anchorDate 参数,从决策信号/龙虎榜跳入时,
// K 线 Modal 默认聚焦到该行日期附近(而非最新 6 个月)。
interface DrillState {
  open: boolean
  tsCode: string | null
  /** 锚定日期 YYYY-MM-DD,用于 StockDrillModal 默认显示该日期±区间 */
  anchorDate: string | null
}

const state = reactive<DrillState>({ open: false, tsCode: null, anchorDate: null })

export function useStockDrill() {
  return {
    state,
    /**
     * 打开股票抽屉。
     * @param ts_code 股票代码
     * @param anchor_date 可选,锚定日期(YYYY-MM-DD),用于默认视图聚焦
     */
    open(ts_code: string, anchor_date?: string | null) {
      state.tsCode = ts_code
      state.anchorDate = anchor_date ?? null
      state.open = true
    },
    close() {
      state.open = false
    },
  }
}
