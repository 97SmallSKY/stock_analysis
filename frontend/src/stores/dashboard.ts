import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchDashboard, type DashboardResp } from '@/api/dashboard'

// Dashboard store: 单飞请求 + 错误状态, 避免多个组件并发拉取
export const useDashboardStore = defineStore('dashboard', () => {
  const data = ref<DashboardResp | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let inflight: Promise<DashboardResp> | null = null

  async function load(topN = 10, force = false): Promise<DashboardResp> {
    if (inflight && !force) return inflight
    loading.value = true
    error.value = null
    inflight = fetchDashboard(topN)
      .then((resp) => {
        data.value = resp
        return resp
      })
      .catch((e) => {
        error.value = String(e?.message ?? e)
        throw e
      })
      .finally(() => {
        loading.value = false
        inflight = null
      })
    return inflight
  }

  return { data, loading, error, load }
})
