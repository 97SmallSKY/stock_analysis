<template>
  <div class="backtest-detail-page">
    <a-card :bordered="false">
      <template #title>
        <a-space>
          <span>回测任务详情</span>
          <a-tag v-if="detail" :color="statusColor(detail.status)">{{ detail.status }}</a-tag>
          <a-typography-text v-if="detail" type="secondary">
            {{ detail.strategy_name }} · {{ detail.start_date }} ~ {{ detail.end_date }}
          </a-typography-text>
        </a-space>
      </template>
      <template #extra>
        <a-button @click="router.push('/backtest-history')">返回历史列表</a-button>
      </template>

      <a-skeleton v-if="loading" active />
      <a-alert v-else-if="error" type="error" :message="error" show-icon />

      <template v-else-if="detail">
        <a-row :gutter="12" class="kpi-row">
          <a-col :span="6">
            <a-statistic
              title="总收益率"
              :value="pctOr(detail.metrics?.total_return)"
              :precision="2"
              suffix="%"
              :value-style="{ color: (detail.metrics?.total_return ?? 0) >= 0 ? '#d4380d' : '#389e0d' }"
            />
          </a-col>
          <a-col :span="6">
            <a-statistic
              title="年化收益"
              :value="pctOr(detail.metrics?.annual_return)"
              :precision="2"
              suffix="%"
            />
          </a-col>
          <a-col :span="6">
            <a-statistic
              title="最大回撤"
              :value="pctOr(detail.metrics?.max_drawdown)"
              :precision="2"
              suffix="%"
              :value-style="{ color: '#389e0d' }"
            />
          </a-col>
          <a-col :span="6">
            <a-statistic
              title="Sharpe"
              :value="detail.metrics?.sharpe_ratio ?? 0"
              :precision="2"
            />
          </a-col>
        </a-row>

        <div class="chart-section">
          <h3>收益曲线 (nav)</h3>
          <VChart
            v-if="navOption"
            :option="navOption"
            class="chart"
            autoresize
          />
          <a-empty v-else description="暂无 nav_series 数据" />
        </div>

        <div class="chart-section">
          <h3>回撤曲线 (drawdown)</h3>
          <VChart
            v-if="ddOption"
            :option="ddOption"
            class="chart"
            autoresize
          />
          <a-empty v-else description="暂无数据" />
        </div>

        <div class="chart-section">
          <h3>买卖点 (nav + markPoint)</h3>
          <VChart
            v-if="tradeMarkOption"
            :option="tradeMarkOption"
            class="chart"
            autoresize
          />
          <a-empty v-else description="暂无 trades_sample 数据(仅新跑任务有)" />
          <div class="trade-stats">
            <a-typography-text type="secondary">
              任务交易总数:{{ detail.result?.trade_count ?? '-' }}
              <template v-if="detail.result?.trades_sample?.length">
                · 显示前 {{ detail.result.trades_sample.length }} 笔
              </template>
            </a-typography-text>
          </div>
        </div>
      </template>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, LegendComponent, TitleComponent,
  DataZoomComponent, MarkPointComponent,
} from 'echarts/components'
import { fetchBacktestTask, type BacktestTaskDetail, type TradeSample } from '@/api/backtest'
import { computeDrawdown } from '@/utils/backtest_drawdown'

use([
  CanvasRenderer, LineChart,
  GridComponent, TooltipComponent, LegendComponent, TitleComponent,
  DataZoomComponent, MarkPointComponent,
])

const route = useRoute()
const router = useRouter()
const detail = ref<BacktestTaskDetail | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

function pctOr(v: number | undefined | null): number {
  return v == null ? 0 : v * 100
}

function statusColor(s: string) {
  if (s === 'SUCCESS') return 'green'
  if (s === 'RUNNING') return 'blue'
  if (s === 'FAILED') return 'red'
  return 'default'
}

const navOption = computed(() => {
  const nav = detail.value?.result?.nav_series
  if (!nav || nav.length === 0) return null
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['nav'] },
    xAxis: { type: 'category', data: nav.map((p) => p.date) },
    yAxis: { type: 'value', scale: true },
    series: [{
      name: 'nav',
      type: 'line',
      showSymbol: false,
      data: nav.map((p) => p.nav),
      lineStyle: { color: '#d4380d', width: 2 },
    }],
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20 }],
  }
})

const ddOption = computed(() => {
  const nav = detail.value?.result?.nav_series
  if (!nav || nav.length === 0) return null
  const dd = computeDrawdown(nav)
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['drawdown'] },
    xAxis: { type: 'category', data: dd.map((p) => p.date) },
    yAxis: {
      type: 'value',
      axisLabel: { formatter: (v: number) => (v * 100).toFixed(1) + '%' },
    },
    series: [{
      name: 'drawdown',
      type: 'line',
      areaStyle: { color: 'rgba(56, 158, 13, 0.15)' },
      showSymbol: false,
      data: dd.map((p) => p.drawdown),
      lineStyle: { color: '#389e0d', width: 2 },
    }],
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20 }],
  }
})

const tradeMarkOption = computed(() => {
  const nav = detail.value?.result?.nav_series
  const trades = detail.value?.result?.trades_sample
  if (!nav || nav.length === 0 || !trades || trades.length === 0) return null
  const navByDate = new Map(nav.map((p) => [p.date, p.nav]))
  const markData = trades
    .filter((t: TradeSample) => navByDate.has(t.date))
    .map((t: TradeSample) => ({
      coord: [t.date, navByDate.get(t.date)!],
      value: t.direction === 'BUY' ? '▲' : '▼',
      itemStyle: { color: t.direction === 'BUY' ? '#d4380d' : '#389e0d' },
    }))
  return {
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: nav.map((p) => p.date) },
    yAxis: { type: 'value', scale: true },
    series: [{
      name: 'nav',
      type: 'line',
      showSymbol: false,
      data: nav.map((p) => p.nav),
      lineStyle: { color: '#d4380d', width: 2 },
      markPoint: {
        symbolSize: 24,
        label: { fontSize: 10, color: '#fff' },
        data: markData,
      },
    }],
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20 }],
  }
})

async function load() {
  const taskId = route.params.taskId as string
  if (!taskId) {
    error.value = '缺少 task_id'
    return
  }
  loading.value = true
  error.value = null
  try {
    detail.value = await fetchBacktestTask(taskId)
  } catch (e: unknown) {
    error.value = (e as { message?: string })?.message ?? String(e)
  } finally {
    loading.value = false
  }
}

onMounted(load)

defineExpose({ detail, loading, error, navOption, ddOption })
</script>

<style scoped>
.backtest-detail-page { padding: 12px; }
.kpi-row { margin-bottom: 24px; }
.chart-section { margin-bottom: 32px; }
.chart-section h3 { margin-bottom: 12px; }
.chart { height: 320px; }
.trade-stats { margin-top: 12px; }
</style>
