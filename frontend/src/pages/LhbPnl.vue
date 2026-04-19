<template>
  <div class="lhb-pnl-page">
    <a-card :bordered="false" :body-style="{ paddingBottom: 8 }">
      <a-form layout="inline" :model="form" @submit.prevent="onSearch">
        <a-form-item label="日期">
          <a-range-picker
            v-model:value="form.range"
            value-format="YYYY-MM-DD"
            allow-clear
          />
        </a-form-item>
        <a-form-item label="TopN">
          <a-input-number v-model:value="form.topN" :min="5" :max="30" style="width: 90px;" />
        </a-form-item>
        <a-form-item>
          <a-space>
            <a-button type="primary" html-type="submit" :loading="loading">分析</a-button>
            <a-button @click="onReset">重置</a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-card>

    <a-card :bordered="false" style="margin-top: 12px;" title="按上榜原因聚合(净买入 / 上榜次数 / 平均评分)">
      <a-empty v-if="!loading && reasonAgg.length === 0" description="无数据" />
      <div v-else ref="chartRef" style="width: 100%; height: 380px;"></div>
    </a-card>

    <a-row :gutter="12" style="margin-top: 12px;">
      <a-col :span="12">
        <a-card :bordered="false" :title="`净买入 Top ${form.topN}`">
          <a-table
            :data-source="topBuy"
            :columns="stockColumns"
            :pagination="false"
            :row-key="(r: StockAgg) => r.ts_code"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'ts_code'">
                <StockLink :code="record.ts_code" :name="record.name" />
              </template>
              <template v-else-if="['net_sum','buy_sum','sell_sum'].includes(column.key)">
                <span :style="column.key === 'net_sum' ? { color: record.net_sum >= 0 ? '#d4380d' : '#389e0d' } : {}">
                  {{ fmtAmountWan(record[column.key]) }}
                </span>
              </template>
              <template v-else-if="column.key === 'avg_score'">
                {{ record.avg_score != null ? record.avg_score.toFixed(1) : '-' }}
              </template>
            </template>
          </a-table>
        </a-card>
      </a-col>
      <a-col :span="12">
        <a-card :bordered="false" :title="`净卖出 Top ${form.topN}`">
          <a-table
            :data-source="topSell"
            :columns="stockColumns"
            :pagination="false"
            :row-key="(r: StockAgg) => r.ts_code"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'ts_code'">
                <StockLink :code="record.ts_code" :name="record.name" />
              </template>
              <template v-else-if="['net_sum','buy_sum','sell_sum'].includes(column.key)">
                <span :style="column.key === 'net_sum' ? { color: record.net_sum >= 0 ? '#d4380d' : '#389e0d' } : {}">
                  {{ fmtAmountWan(record[column.key]) }}
                </span>
              </template>
              <template v-else-if="column.key === 'avg_score'">
                {{ record.avg_score != null ? record.avg_score.toFixed(1) : '-' }}
              </template>
            </template>
          </a-table>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import {
  GridComponent, LegendComponent, TooltipComponent, TitleComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { fetchLhb, type LhbRow } from '@/api/lhb'
import { fmtAmountWan } from '@/utils/format'
import StockLink from '@/components/StockLink.vue'

echarts.use([BarChart, GridComponent, LegendComponent, TooltipComponent, TitleComponent, CanvasRenderer])

interface FormState {
  range: [string, string] | null
  topN: number
}
const form = reactive<FormState>({ range: null, topN: 10 })

const loading = ref(false)
const rows = ref<LhbRow[]>([])

interface ReasonAgg {
  reason: string
  net_sum: number
  count: number
  avg_score: number | null
}
interface StockAgg {
  ts_code: string
  name: string | null
  net_sum: number
  buy_sum: number
  sell_sum: number
  count: number
  avg_score: number | null
}

const stockColumns = [
  {
    title: '股票', dataIndex: 'ts_code', key: 'ts_code', width: 180,
    sorter: (a: StockAgg, b: StockAgg) => a.ts_code.localeCompare(b.ts_code),
  },
  {
    title: '上榜次数', dataIndex: 'count', key: 'count', width: 90, align: 'right' as const,
    sorter: (a: StockAgg, b: StockAgg) => a.count - b.count,
  },
  {
    title: '买入合计', dataIndex: 'buy_sum', key: 'buy_sum', align: 'right' as const,
    sorter: (a: StockAgg, b: StockAgg) => a.buy_sum - b.buy_sum,
  },
  {
    title: '卖出合计', dataIndex: 'sell_sum', key: 'sell_sum', align: 'right' as const,
    sorter: (a: StockAgg, b: StockAgg) => a.sell_sum - b.sell_sum,
  },
  {
    title: '净额', dataIndex: 'net_sum', key: 'net_sum', align: 'right' as const,
    sorter: (a: StockAgg, b: StockAgg) => a.net_sum - b.net_sum,
  },
  {
    title: '平均评分', dataIndex: 'avg_score', key: 'avg_score', width: 90, align: 'right' as const,
    sorter: (a: StockAgg, b: StockAgg) => (a.avg_score ?? -1) - (b.avg_score ?? -1),
  },
]

async function load() {
  loading.value = true
  try {
    const data = await fetchLhb({
      date_from: form.range?.[0]?.replace(/-/g, '') || undefined,
      date_to: form.range?.[1]?.replace(/-/g, '') || undefined,
      order_by: 'trade_date',
      order_desc: true,
      limit: 1000,
    })
    rows.value = data.rows ?? []
  } catch {
    /* toast 已处理 */
  } finally {
    loading.value = false
  }
}

function onSearch() { load() }
function onReset() {
  form.range = null
  form.topN = 10
  load()
}

const reasonAgg = computed<ReasonAgg[]>(() => {
  const m = new Map<string, { net: number; cnt: number; scores: number[] }>()
  for (const r of rows.value) {
    const k = (r.reason ?? '').trim() || '(未知)'
    const cur = m.get(k) ?? { net: 0, cnt: 0, scores: [] }
    cur.net += r.net_amount ?? 0
    cur.cnt += 1
    if (r.score != null) cur.scores.push(r.score)
    m.set(k, cur)
  }
  const arr: ReasonAgg[] = [...m.entries()].map(([reason, v]) => ({
    reason,
    net_sum: v.net,
    count: v.cnt,
    avg_score: v.scores.length ? v.scores.reduce((a, b) => a + b, 0) / v.scores.length : null,
  }))
  // 只展示 Top 10 reason, 按净额绝对值排序
  arr.sort((a, b) => Math.abs(b.net_sum) - Math.abs(a.net_sum))
  return arr.slice(0, 10)
})

const stockAgg = computed<StockAgg[]>(() => {
  const m = new Map<string, { name: string | null; buy: number; sell: number; net: number; cnt: number; scores: number[] }>()
  for (const r of rows.value) {
    const cur = m.get(r.ts_code) ?? { name: null, buy: 0, sell: 0, net: 0, cnt: 0, scores: [] }
    if (!cur.name && r.name) cur.name = r.name
    cur.buy += r.buy_amount ?? 0
    cur.sell += r.sell_amount ?? 0
    cur.net += r.net_amount ?? 0
    cur.cnt += 1
    if (r.score != null) cur.scores.push(r.score)
    m.set(r.ts_code, cur)
  }
  return [...m.entries()].map(([ts_code, v]) => ({
    ts_code,
    name: v.name,
    buy_sum: v.buy,
    sell_sum: v.sell,
    net_sum: v.net,
    count: v.cnt,
    avg_score: v.scores.length ? v.scores.reduce((a, b) => a + b, 0) / v.scores.length : null,
  }))
})

const topBuy = computed(() =>
  [...stockAgg.value].sort((a, b) => b.net_sum - a.net_sum).slice(0, form.topN),
)
const topSell = computed(() =>
  [...stockAgg.value].sort((a, b) => a.net_sum - b.net_sum).slice(0, form.topN),
)

// ---- ECharts ----
const chartRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

function renderChart() {
  if (!chartRef.value) return
  if (!chart) chart = echarts.init(chartRef.value)
  const data = reasonAgg.value
  chart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['净买入(亿)', '上榜次数', '平均评分'], top: 0 },
    grid: { left: 50, right: 50, top: 30, bottom: 80 },
    xAxis: {
      type: 'category',
      data: data.map((d) => d.reason),
      axisLabel: { rotate: 28, interval: 0, width: 120, overflow: 'truncate' },
    },
    yAxis: [
      { type: 'value', name: '亿元', position: 'left' },
      { type: 'value', name: '次数 / 分', position: 'right' },
    ],
    series: [
      {
        name: '净买入(亿)', type: 'bar',
        data: data.map((d) => +(d.net_sum / 1e8).toFixed(2)),
        itemStyle: {
          color: (p: { data: number }) => (p.data >= 0 ? '#d4380d' : '#389e0d'),
        },
      },
      { name: '上榜次数', type: 'bar', yAxisIndex: 1, data: data.map((d) => d.count) },
      {
        name: '平均评分', type: 'bar', yAxisIndex: 1,
        data: data.map((d) => (d.avg_score != null ? +d.avg_score.toFixed(1) : 0)),
      },
    ],
  }, true)
}

function onResize() { chart?.resize() }

watch(reasonAgg, async () => { await nextTick(); renderChart() })

onMounted(async () => {
  await load()
  await nextTick()
  renderChart()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.lhb-pnl-page { padding: 12px; }
</style>
