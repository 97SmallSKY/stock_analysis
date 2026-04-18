<template>
  <div class="backtest-page">
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  compareBacktests, listStrategies,
  type CompareStrategy, type ComparisonReport,
} from '@/api/backtest'
import { fmtAmount, fmtPct } from '@/utils/format'
import { downloadCsv, rowsToCsv } from '@/utils/csv'

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

onMounted(refreshStrategies)
</script>

<style scoped>
.winner-tag { color: #d4380d; }
</style>
