import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  fetchLhb, fetchLhbSeats,
  type LhbListParams, type LhbListResp,
  type LhbSeatRow, type LhbSeatParams,
} from '@/api/lhb'

// LHB 列表 store: 保存当前 filter + 分页游标
export const useLhbStore = defineStore('lhb', () => {
  const list = ref<LhbListResp>({ total: 0, limit: 50, offset: 0, rows: [] })
  const loading = ref(false)
  const filter = ref<LhbListParams>({
    limit: 50,
    offset: 0,
    order_by: 'trade_date',
    order_desc: true,
    // P16.5: 龙虎榜主列表默认带 T+N 收益字段, 便于 AG Grid 展示
    with_tn: true,
  })

  async function search(partial?: Partial<LhbListParams>, resetOffset = true) {
    filter.value = {
      ...filter.value,
      ...partial,
      ...(resetOffset ? { offset: 0 } : {}),
    }
    loading.value = true
    try {
      const resp = await fetchLhb(filter.value)
      list.value = resp
      return resp
    } finally {
      loading.value = false
    }
  }

  async function page(offset: number, limit?: number) {
    return search({ offset, limit: limit ?? filter.value.limit }, false)
  }

  // 席位 drawer: 临时查询, 不写入 list
  const seats = ref<LhbSeatRow[]>([])
  const seatsLoading = ref(false)
  async function loadSeats(params: LhbSeatParams) {
    seatsLoading.value = true
    try {
      const resp = await fetchLhbSeats(params)
      seats.value = resp.rows
      return resp
    } finally {
      seatsLoading.value = false
    }
  }

  return {
    list, loading, filter, search, page,
    seats, seatsLoading, loadSeats,
  }
})
