import { reactive } from 'vue'

// 单例 store: 全局只挂一个 StockDrillModal, 任何组件通过 open(ts_code) 打开
interface DrillState {
  open: boolean
  tsCode: string | null
}

const state = reactive<DrillState>({ open: false, tsCode: null })

export function useStockDrill() {
  return {
    state,
    open(ts_code: string) {
      state.tsCode = ts_code
      state.open = true
    },
    close() {
      state.open = false
    },
  }
}
