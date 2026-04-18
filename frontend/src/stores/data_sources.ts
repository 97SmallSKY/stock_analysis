import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  fetchStatus, setPriority, retryAdapter, circuitAction,
  type DataSourceStatus,
} from '@/api/data_sources'

// 数据源管理 store
// - load(): 拉 /status 全量快照
// - applyPriority(category, chain): 提交新优先级链, 成功后乐观更新本地 priority_chains
// - retry/forceCircuit: 单适配器动作
export const useDataSourceStore = defineStore('data_sources', () => {
  const status = ref<DataSourceStatus | null>(null)
  const loading = ref(false)

  async function load() {
    loading.value = true
    try {
      status.value = await fetchStatus()
      return status.value
    } finally {
      loading.value = false
    }
  }

  async function applyPriority(category: string, chain: string[]) {
    await setPriority(category, chain)
    if (status.value) {
      status.value.priority_chains = {
        ...status.value.priority_chains,
        [category]: [...chain],
      }
    }
  }

  async function retry(name: string) {
    const resp = await retryAdapter(name)
    // 后续 load() 会拉真实状态,这里只做局部 initialized 标记
    if (status.value) {
      const a = status.value.adapters.find((x) => x.name === name)
      if (a) a.initialized = Boolean(resp?.initialized)
    }
    return resp
  }

  async function forceCircuit(name: string, action: 'open' | 'close') {
    const resp = await circuitAction(name, action)
    if (status.value) {
      const a = status.value.adapters.find((x) => x.name === name)
      if (a && resp?.status) a.circuit = resp.status
    }
    return resp
  }

  return { status, loading, load, applyPriority, retry, forceCircuit }
})
