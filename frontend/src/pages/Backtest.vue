<template>
  <div class="backtest-page">
    <a-tabs v-model:active-key="activeTab">
      <a-tab-pane key="compare" tab="策略对比">
    <a-card :bordered="false" title="策略对比 (Compare)">
      <template #extra>
        <a-space>
          <a-button size="small" @click="refreshStrategies" :loading="loadingStrategies">
            刷新策略
          </a-button>
        </a-space>
      </template>

      <a-row :gutter="12">
        <a-col :span="6">
          <a-form-item label="日期范围">
            <a-range-picker
              v-model:value="range"
              value-format="YYYY-MM-DD"
              :disabled="running"
              style="width: 100%;"
            />
          </a-form-item>
        </a-col>
        <a-col :span="4">
          <a-form-item label="初始资金">
            <a-input-number
              v-model:value="initialCapital"
              :step="100000"
              :min="10000"
              :disabled="running"
              style="width: 100%;"
            />
          </a-form-item>
        </a-col>
        <a-col :span="3">
          <a-form-item label="最大持仓">
            <a-input-number
              v-model:value="maxPositions"
              :min="1" :max="20"
              :disabled="running"
              style="width: 100%;"
            />
          </a-form-item>
        </a-col>
        <a-col :span="3">
          <a-form-item label="单仓比例">
            <a-input-number
              v-model:value="perPositionPct"
              :min="0.05" :max="1" :step="0.05"
              :disabled="running"
              style="width: 100%;"
            />
          </a-form-item>
        </a-col>
        <a-col :span="8">
          <a-form-item label="候选策略">
            <a-select
              v-model:value="pickedStrategies"
              mode="multiple"
              :options="strategyOptions"
              placeholder="选 >= 1 个策略"
              :disabled="running"
              style="width: 100%;"
            />
          </a-form-item>
        </a-col>
      </a-row>

      <a-space style="margin-top: 8px;">
        <a-button
          type="primary"
          :loading="running"
          :disabled="!canRun"
          @click="runCompare"
        >
          运行对比
        </a-button>
        <a-button :disabled="!report" @click="exportCsv">导出 CSV</a-button>
        <span v-if="report?.winner" class="winner-tag">
          🏆 最佳: <strong>{{ report.winner }}</strong>
        </span>
      </a-space>
    </a-card>

    <a-card
      v-if="report"
      :bordered="false"
      style="margin-top: 12px;"
      title="结果"
    >
      <a-table
        :columns="cols"
        :data-source="report.runs"
        :row-key="(r: any) => r.name"
        :pagination="false"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'ok'">
            <a-tag :color="record.ok ? 'green' : 'red'">
              {{ record.ok ? 'OK' : '失败' }}
            </a-tag>
          </template>
          <template v-else-if="['annual_return','total_return'].includes(column.key)">
            <span :style="{ color: (record[column.key] ?? 0) >= 0 ? '#d4380d' : '#52c41a' }">
              {{ fmtPct((record[column.key] ?? 0) * 100) }}
            </span>
          </template>
          <template v-else-if="column.key === 'max_drawdown'">
            {{ fmtPct((record.max_drawdown ?? 0) * 100) }}
          </template>
          <template v-else-if="column.key === 'win_rate'">
            {{ fmtPct((record.win_rate ?? 0) * 100) }}
          </template>
          <template v-else-if="column.key === 'sharpe_ratio'">
            {{ (record.sharpe_ratio ?? 0).toFixed(2) }}
          </template>
          <template v-else-if="column.key === 'final_assets'">
            {{ fmtAmount(record.final_assets) }}
          </template>
        </template>
      </a-table>
    </a-card>
      </a-tab-pane>

      <a-tab-pane key="history" tab="历史任务">
        <a-card :bordered="false">
          <template #title>
            <span>历史回测任务</span>
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
              />
              <a-select
                v-model:value="historyFilter.strategy_name"
                placeholder="策略"
                allow-clear
                style="width: 200px;"
                :options="strategyOptions"
              />
              <a-button :loading="historyLoading" @click="loadHistory">刷新</a-button>
            </a-space>
          </template>
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
      </a-tab-pane>
    </a-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  compareBacktests, listStrategies, fetchBacktestHistory,
  type CompareStrategy, type ComparisonReport, type BacktestHistoryRow,
} from '@/api/backtest'
import { fmtAmount, fmtPct } from '@/utils/format'
import { downloadCsv, rowsToCsv } from '@/utils/csv'

const activeTab = ref<'compare' | 'history'>('compare')

const range = ref<[string, string] | null>(null)
const initialCapital = ref(1_000_000)
const maxPositions = ref(5)
const perPositionPct = ref(0.2)
const pickedStrategies = ref<string[]>([])
const strategyOptions = ref<{ value: string; label: string }[]>([])
const running = ref(false)
const loadingStrategies = ref(false)
const report = ref<ComparisonReport | null>(null)

const cols = [
  { title: '名称', dataIndex: 'name', key: 'name' },
  { title: '状态', key: 'ok', width: 70 },
  { title: '总收益', dataIndex: 'total_return', key: 'total_return', width: 100, align: 'right' },
  { title: '年化', dataIndex: 'annual_return', key: 'annual_return', width: 100, align: 'right' },
  { title: 'Sharpe', key: 'sharpe_ratio', width: 90, align: 'right' },
  { title: '最大回撤', key: 'max_drawdown', width: 100, align: 'right' },
  { title: '胜率', key: 'win_rate', width: 90, align: 'right' },
  { title: '交易数', dataIndex: 'trade_count', key: 'trade_count', width: 80, align: 'right' },
  { title: '终值', key: 'final_assets', width: 140, align: 'right' },
  { title: '耗时(ms)', dataIndex: 'duration_ms', key: 'duration_ms', width: 100, align: 'right' },
]

const canRun = computed(() =>
  !!range.value && range.value.length === 2 && pickedStrategies.value.length > 0,
)

async function refreshStrategies() {
  loadingStrategies.value = true
  try {
    const r = await listStrategies()
    strategyOptions.value = (r.strategies || []).map((s) => ({ value: s, label: s }))
  } finally {
    loadingStrategies.value = false
  }
}

async function runCompare() {
  if (!canRun.value || !range.value) return
  running.value = true
  try {
    const strategies: CompareStrategy[] = pickedStrategies.value.map((s, i) => ({
      name: `${s}#${i + 1}`, strategy_name: s,
    }))
    report.value = await compareBacktests({
      start_date: range.value[0],
      end_date: range.value[1],
      initial_capital: initialCapital.value,
      strategies,
      max_positions: maxPositions.value,
      per_position_pct: perPositionPct.value,
    })
    message.success(`已跑 ${report.value.runs.length} 个策略`)
  } catch {
    // http 拦截器 toast
  } finally {
    running.value = false
  }
}

function exportCsv() {
  if (!report.value) return
  const csv = rowsToCsv(report.value.runs as unknown as Record<string, unknown>[])
  const name = `compare_${report.value.start_date}_${report.value.end_date}.csv`
  downloadCsv(name, csv)
}

// ---- P7.1 历史任务 Tab ----
const historyRows = ref<BacktestHistoryRow[]>([])
const historyLoading = ref(false)
const historyFilter = reactive<{ status?: string; strategy_name?: string }>({})

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

watch(activeTab, (v) => { if (v === 'history' && historyRows.value.length === 0) loadHistory() })

onMounted(refreshStrategies)
</script>

<style scoped>
.winner-tag { color: #d4380d; }
</style>
