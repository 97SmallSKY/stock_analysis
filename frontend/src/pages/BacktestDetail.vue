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

        <!-- STO-131: 交易标的说明 -->
        <div v-if="detail.result?.trades_sample?.length" style="margin-bottom:16px;">
          <a-typography-text type="secondary" style="font-size:12px;">
            交易标的：{{ tradeSymbolsSummary }}
          </a-typography-text>
        </div>
        <div v-else style="margin-bottom:16px;">
          <a-typography-text type="secondary" style="font-size:12px;">
            交易标的：策略根据龙虎榜信号自动选股（全市场龙虎榜标的）
          </a-typography-text>
        </div>

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
          <h3>买卖点 (K线 + 买卖箭头)</h3>
          <div v-if="tradeStocks.length > 1" style="margin-bottom:8px;">
            <a-select v-model:value="selectedStock" style="width:160px;" size="small">
              <a-select-option v-for="s in tradeStocks" :key="s" :value="s">{{ s }}</a-select-option>
            </a-select>
          </div>
          <VChart
            v-if="tradeKlineOption"
            :option="tradeKlineOption"
            class="chart chart-tall"
            autoresize
          />
          <a-empty v-else description="暂无 trades_sample 数据或 K 线加载中(仅新跑任务有)"
            style="padding:32px 0" />
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
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, CandlestickChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, LegendComponent, TitleComponent,
  DataZoomComponent, MarkPointComponent,
} from 'echarts/components'
import { fetchBacktestTask, type BacktestTaskDetail, type TradeSample } from '@/api/backtest'
import { fetchKlineDaily } from '@/api/kline'
import { computeDrawdown } from '@/utils/backtest_drawdown'

use([
  CanvasRenderer, LineChart, CandlestickChart,
  GridComponent, TooltipComponent, LegendComponent, TitleComponent,
  DataZoomComponent, MarkPointComponent,
])

const route = useRoute()
const router = useRouter()
const detail = ref<BacktestTaskDetail | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

// STO-130: K线 + 买卖箭头
const klineRows = ref<{ date: string; open: number; high: number; low: number; close: number }[]>([])
const selectedStock = ref<string>('')

const tradeStocks = computed(() => {
  // STO-131: 优先读后端返回的 trade_targets，回退到 trades_sample 推导
  const targets = detail.value?.trade_targets
  if (targets && targets.length > 0) return targets
  const trades = detail.value?.result?.trades_sample
  if (!trades) return []
  return [...new Set(trades.map((t: TradeSample) => t.ts_code))]
})

// STO-131: 交易标的摘要
const tradeSymbolsSummary = computed(() => {
  const stocks = tradeStocks.value
  if (stocks.length === 0) return '无'
  if (stocks.length === 1) return stocks[0]
  if (stocks.length <= 3) return stocks.join(', ')
  return `${stocks.slice(0, 3).join(', ')} 等 ${stocks.length} 只股票`
})

watch([tradeStocks, detail], async () => {
  if (!tradeStocks.value.length || !detail.value) return
  if (!selectedStock.value || !tradeStocks.value.includes(selectedStock.value)) {
    selectedStock.value = tradeStocks.value[0]
  }
  // 拉取选中股票的 K 线
  const fromDate = detail.value.start_date?.replace(/-/g, '') ?? ''
  const toDate = detail.value.end_date?.replace(/-/g, '') ?? ''
  if (!fromDate || !toDate) return
  try {
    const resp = await fetchKlineDaily(selectedStock.value, fromDate, toDate, 2000)
    klineRows.value = resp.rows as { date: string; open: number; high: number; low: number; close: number }[]
  } catch {
    klineRows.value = []
  }
}, { immediate: false })

watch(selectedStock, async (stock) => {
  if (!stock || !detail.value) return
  const fromDate = detail.value.start_date?.replace(/-/g, '') ?? ''
  const toDate = detail.value.end_date?.replace(/-/g, '') ?? ''
  if (!fromDate || !toDate) return
  try {
    const resp = await fetchKlineDaily(stock, fromDate, toDate, 2000)
    klineRows.value = resp.rows as { date: string; open: number; high: number; low: number; close: number }[]
  } catch {
    klineRows.value = []
  }
})

const tradeKlineOption = computed(() => {
  const trades = detail.value?.result?.trades_sample
  if (!trades || trades.length === 0 || klineRows.value.length === 0) return null
  const stockTrades = trades.filter((t: TradeSample) => t.ts_code === selectedStock.value)
  const dates = klineRows.value.map((r) => r.date)
  const kdata = klineRows.value.map((r) => [r.open, r.close, r.low, r.high])
  const markData = stockTrades.map((t: TradeSample) => ({
    coord: [t.date, t.price],
    name: t.direction,
    value: t.direction === 'BUY' ? '买' : '卖',
    itemStyle: { color: t.direction === 'BUY' ? '#389e0d' : '#d4380d' },
    label: {
      show: true,
      position: t.direction === 'BUY' ? 'bottom' : 'top',
      color: t.direction === 'BUY' ? '#389e0d' : '#d4380d',
      fontSize: 11,
      formatter: t.direction === 'BUY' ? '↑买' : '↓卖',
    },
    symbol: 'none',
  }))
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    xAxis: { type: 'category', data: dates, boundaryGap: true },
    yAxis: { scale: true, splitLine: { show: true } },
    series: [{
      name: selectedStock.value,
      type: 'candlestick',
      data: kdata,
      itemStyle: {
        color: '#d4380d', color0: '#389e0d',
        borderColor: '#d4380d', borderColor0: '#389e0d',
      },
      markPoint: { symbol: 'none', symbolSize: 20, data: markData },
    }],
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20 }],
  }
})

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
.chart-tall { height: 420px; }
.trade-stats { margin-top: 12px; }
</style>
