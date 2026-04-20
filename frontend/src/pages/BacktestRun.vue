<template>
  <div class="backtest-run-page">
    <a-card :bordered="false">
      <template #title>
        <span>回测任务 · 历史记录</span>
      </template>
      <template #extra>
        <a-space>
          <a-select
            v-model:value="historyFilter.status"
            placeholder="状态"
            allow-clear
            style="width: 120px;"
            :options="[
              { value: 'PENDING', label: 'PENDING' },
              { value: 'RUNNING', label: 'RUNNING' },
              { value: 'SUCCESS', label: 'SUCCESS' },
              { value: 'FAILED', label: 'FAILED' },
            ]"
            @change="loadHistory"
          />
          <a-select
            v-model:value="historyFilter.strategy_name"
            placeholder="策略"
            allow-clear
            style="width: 200px;"
            :options="strategyOptions"
            @change="loadHistory"
          />
          <a-button :loading="historyLoading" @click="loadHistory">刷新</a-button>
          <a-button @click="goCompare">去策略对比</a-button>
        </a-space>
      </template>

      <a-row :gutter="12" class="kpi-row" v-if="kpi.total > 0">
        <a-col :span="6">
          <a-statistic title="总任务" :value="kpi.total" />
        </a-col>
        <a-col :span="6">
          <a-statistic
            title="成功率"
            :value="kpi.successRate"
            :precision="1"
            suffix="%"
            :value-style="{ color: kpi.successRate >= 80 ? '#d4380d' : '#262626' }"
          />
        </a-col>
        <a-col :span="6">
          <a-statistic
            title="平均收益"
            :value="kpi.avgReturn"
            :precision="2"
            suffix="%"
            :value-style="{ color: kpi.avgReturn >= 0 ? '#d4380d' : '#389e0d' }"
          />
        </a-col>
        <a-col :span="6">
          <a-statistic
            title="最佳 Sharpe"
            :value="kpi.bestSharpe"
            :precision="2"
          />
        </a-col>
      </a-row>

      <a-table
        :data-source="historyRows"
        :columns="historyColumns"
        :loading="historyLoading"
        :pagination="{ pageSize: 20, showSizeChanger: false }"
        :row-key="(r: BacktestHistoryRow) => r.task_id"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'status'">
            <a-tag :color="statusColor(record.status)">{{ record.status }}</a-tag>
          </template>
          <template v-else-if="column.key === 'total_return'">
            <span :style="{ color: (record.total_return ?? 0) >= 0 ? '#d4380d' : '#389e0d' }">
              {{ fmtPct(record.total_return) }}
            </span>
          </template>
          <template v-else-if="column.key === 'max_drawdown'">
            <span style="color: #389e0d;">{{ fmtPct(record.max_drawdown) }}</span>
          </template>
          <template v-else-if="column.key === 'sharpe'">
            {{ record.sharpe != null ? Number(record.sharpe).toFixed(2) : '-' }}
          </template>
          <template v-else-if="column.key === 'win_rate'">
            {{ fmtPct(record.win_rate) }}
          </template>
          <template v-else-if="column.key === 'elapsed_seconds'">
            {{ record.elapsed_seconds != null ? record.elapsed_seconds.toFixed(1) + ' s' : '-' }}
          </template>
          <template v-else-if="column.key === 'started_at'">
            {{ record.started_at ? record.started_at.replace('T', ' ').slice(0, 19) : '-' }}
          </template>
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  listStrategies, fetchBacktestHistory,
  type BacktestHistoryRow,
} from '@/api/backtest'
import { fmtPct } from '@/utils/format'
import { summarizeHistory } from '@/utils/backtest_history'

const router = useRouter()
const historyRows = ref<BacktestHistoryRow[]>([])
const historyLoading = ref(false)
const historyFilter = reactive<{ status?: string; strategy_name?: string }>({})
const strategyOptions = ref<{ value: string; label: string }[]>([])

const historyColumns = [
  { title: 'task_id', dataIndex: 'task_id', key: 'task_id', width: 280, ellipsis: true },
  { title: '策略', dataIndex: 'strategy_name', key: 'strategy_name', width: 160,
    sorter: (a: BacktestHistoryRow, b: BacktestHistoryRow) =>
      a.strategy_name.localeCompare(b.strategy_name) },
  { title: '状态', key: 'status', width: 100 },
  { title: '区间', key: 'range', width: 200,
    customRender: ({ record }: { record: BacktestHistoryRow }) =>
      `${record.start_date} ~ ${record.end_date}` },
  { title: '总收益', key: 'total_return', width: 110, align: 'right' as const,
    sorter: (a: BacktestHistoryRow, b: BacktestHistoryRow) =>
      (a.total_return ?? -1e9) - (b.total_return ?? -1e9) },
  { title: 'Sharpe', key: 'sharpe', width: 90, align: 'right' as const,
    sorter: (a: BacktestHistoryRow, b: BacktestHistoryRow) =>
      (a.sharpe ?? -1e9) - (b.sharpe ?? -1e9) },
  { title: '最大回撤', key: 'max_drawdown', width: 110, align: 'right' as const,
    sorter: (a: BacktestHistoryRow, b: BacktestHistoryRow) =>
      (a.max_drawdown ?? -1e9) - (b.max_drawdown ?? -1e9) },
  { title: '胜率', key: 'win_rate', width: 90, align: 'right' as const,
    sorter: (a: BacktestHistoryRow, b: BacktestHistoryRow) =>
      (a.win_rate ?? -1e9) - (b.win_rate ?? -1e9) },
  { title: '交易数', dataIndex: 'n_trades', key: 'n_trades', width: 90, align: 'right' as const,
    sorter: (a: BacktestHistoryRow, b: BacktestHistoryRow) =>
      (a.n_trades ?? 0) - (b.n_trades ?? 0) },
  { title: '耗时', key: 'elapsed_seconds', width: 90, align: 'right' as const },
  { title: '开始时间', key: 'started_at', width: 170,
    sorter: (a: BacktestHistoryRow, b: BacktestHistoryRow) =>
      (a.started_at ?? '').localeCompare(b.started_at ?? '') },
]

function statusColor(s: string) {
  if (s === 'SUCCESS') return 'green'
  if (s === 'RUNNING') return 'blue'
  if (s === 'FAILED') return 'red'
  return 'default'
}

const kpi = computed(() => summarizeHistory(historyRows.value))

async function loadHistory() {
  historyLoading.value = true
  try {
    const body = await fetchBacktestHistory({
      status: historyFilter.status,
      strategy_name: historyFilter.strategy_name,
      limit: 200,
    })
    historyRows.value = body.rows
  } catch {
    /* toast 已处理 */
  } finally {
    historyLoading.value = false
  }
}

async function refreshStrategies() {
  try {
    const r = await listStrategies()
    // STO-47: 后端改返回富对象 {name, description, category, default_params},
    // 这里只需要下拉 value=name / label=中文描述(空串时 fallback 到代号)
    strategyOptions.value = (r.strategies || []).map((s) => ({
      value: s.name,
      label: s.description || s.name,
    }))
  } catch { /* ignore */ }
}

function goCompare() {
  router.push('/backtest')
}

onMounted(() => {
  refreshStrategies()
  loadHistory()
})
</script>

<style scoped>
.backtest-run-page { padding: 12px; }
.kpi-row { margin-bottom: 16px; }
</style>
