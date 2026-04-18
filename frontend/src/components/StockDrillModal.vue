<template>
  <a-modal
    v-model:open="drill.state.open"
    :title="title"
    width="960px"
    :footer="null"
    :destroy-on-close="true"
    @cancel="drill.close()"
  >
    <a-tabs v-model:active-key="tab" @change="onTabChange">
      <a-tab-pane key="daily" tab="日K" />
      <a-tab-pane key="weekly" tab="周K (5日均)" />
      <a-tab-pane
        key="minute"
        :tab="minuteTabLabel"
        :disabled="!pickedDate"
      />
    </a-tabs>

    <div v-if="pickedDate && tab !== 'daily'" class="bread">
      <a-tag color="blue">选中日期: {{ pickedDate }}</a-tag>
      <a-button type="link" size="small" @click="resetPicked">清除选择</a-button>
    </div>

    <a-spin :spinning="loading">
      <div v-if="emptyReason" style="padding: 60px 0;">
        <a-empty :description="emptyText" />
      </div>
      <div v-else ref="chartRef" class="chart-box"></div>
    </a-spin>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { CandlestickChart, LineChart, BarChart } from 'echarts/charts'
import {
  GridComponent, LegendComponent, TooltipComponent, TitleComponent,
  DataZoomComponent, MarkLineComponent, MarkPointComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useStockDrill } from '@/composables/useStockDrill'
import {
  fetchKlineDaily, fetchKlineWeekly, fetchKlineMinute, type KlineBar,
} from '@/api/kline'

echarts.use([
  CandlestickChart, LineChart, BarChart,
  GridComponent, LegendComponent, TooltipComponent, TitleComponent,
  DataZoomComponent, MarkLineComponent, MarkPointComponent,
  CanvasRenderer,
])

const drill = useStockDrill()
const tab = ref<'daily' | 'weekly' | 'minute'>('daily')
const loading = ref(false)
const rows = ref<KlineBar[]>([])
const emptyReason = ref<string | null>(null)
const pickedDate = ref<string | null>(null)

const chartRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

const title = computed(() => {
  if (!drill.state.tsCode) return '股票下钻'
  return `${drill.state.tsCode} · 行情下钻`
})

const minuteTabLabel = computed(() =>
  pickedDate.value ? `分钟 K (${pickedDate.value})` : '分钟 K (请先在日K选日期)',
)

const emptyText = computed(() => {
  if (!emptyReason.value) return ''
  if (emptyReason.value === 'no_source_table') return '该数据源未接入,待补'
  if (emptyReason.value === 'out_of_range') return '该时间范围内无行情数据'
  return emptyReason.value
})

function resetPicked() {
  pickedDate.value = null
  if (tab.value !== 'daily') tab.value = 'daily'
}

async function load() {
  if (!drill.state.tsCode) return
  loading.value = true
  rows.value = []
  emptyReason.value = null
  try {
    let body
    if (tab.value === 'daily') {
      body = await fetchKlineDaily(drill.state.tsCode)
    } else if (tab.value === 'weekly') {
      body = await fetchKlineWeekly(drill.state.tsCode)
    } else {
      if (!pickedDate.value) {
        emptyReason.value = 'no_pick'
        return
      }
      const ymd = pickedDate.value.replace(/-/g, '')
      body = await fetchKlineMinute(drill.state.tsCode, ymd)
    }
    rows.value = body.rows
    emptyReason.value = body.empty_reason
  } catch {
    emptyReason.value = 'fetch_failed'
  } finally {
    loading.value = false
    await nextTick()
    render()
  }
}

function ma(list: KlineBar[], n: number): (number | null)[] {
  const out: (number | null)[] = []
  for (let i = 0; i < list.length; i++) {
    if (i + 1 < n) { out.push(null); continue }
    let s = 0
    for (let j = i - n + 1; j <= i; j++) s += list[j].close ?? 0
    out.push(+(s / n).toFixed(3))
  }
  return out
}

function render() {
  if (!chartRef.value) return
  if (chart) { chart.dispose(); chart = null }
  if (rows.value.length === 0) return
  chart = echarts.init(chartRef.value)

  const dates = rows.value.map((r) => r.date)
  const kdata = rows.value.map((r) => [r.open ?? 0, r.close ?? 0, r.low ?? 0, r.high ?? 0])

  const series: Record<string, unknown>[] = [
    {
      name: 'K 线', type: 'candlestick', data: kdata,
      itemStyle: {
        color: '#d4380d', color0: '#389e0d',
        borderColor: '#d4380d', borderColor0: '#389e0d',
      },
    },
  ]

  if (tab.value === 'weekly' || tab.value === 'daily') {
    series.push({
      name: 'MA5', type: 'line', data: ma(rows.value, 5),
      smooth: true, symbol: 'none', lineStyle: { width: 1.2, color: '#1677ff' },
    })
  }
  if (tab.value === 'daily') {
    series.push({
      name: 'MA10', type: 'line', data: ma(rows.value, 10),
      smooth: true, symbol: 'none', lineStyle: { width: 1, color: '#fa8c16' },
    })
  }

  chart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: series.map((s) => s.name as string), top: 0 },
    grid: { left: 50, right: 30, top: 40, bottom: 60 },
    xAxis: { type: 'category', data: dates, boundaryGap: true, axisLine: { onZero: false } },
    yAxis: { scale: true, splitLine: { show: true } },
    dataZoom: [
      { type: 'inside', start: 60, end: 100 },
      { type: 'slider', bottom: 10, height: 18, start: 60, end: 100 },
    ],
    series,
  })

  // 点击 K 线 bar -> 记录日期
  chart.off('click')
  chart.on('click', (ev: { seriesType?: string; dataIndex?: number }) => {
    if (ev.seriesType !== 'candlestick') return
    const idx = ev.dataIndex
    if (idx == null) return
    const d = dates[idx]
    if (!d) return
    pickedDate.value = d
    // 按题目:点日K → 切 5日 (周K); 点周K → 切分钟 K
    if (tab.value === 'daily') {
      tab.value = 'weekly'
    } else if (tab.value === 'weekly') {
      tab.value = 'minute'
    }
  })
}

function onTabChange() { load() }

function onResize() { chart?.resize() }
window.addEventListener('resize', onResize)

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose(); chart = null
})

// 打开时 / 切股票时 重置并加载
watch(
  () => [drill.state.open, drill.state.tsCode],
  ([open]) => {
    if (!open) return
    tab.value = 'daily'
    pickedDate.value = null
    load()
  },
  { immediate: false },
)
</script>

<style scoped>
.chart-box { width: 100%; height: 480px; }
.bread { margin-bottom: 8px; }
</style>
