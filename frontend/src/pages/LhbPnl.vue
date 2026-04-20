<template>
  <div class="lhb-pnl-page">
    <a-card :bordered="false" :body-style="{ paddingBottom: 8 }">
      <a-form layout="inline" :model="form" @submit.prevent="onSearch">
        <a-form-item label="日期">
          <!-- P19.6c (2026-04-20): 日期范围快捷选择 -->
          <DateRangeQuickPick v-model="form.range" />
        </a-form-item>
        <a-form-item label="TopN">
          <a-input-number v-model:value="form.topN" :min="5" :max="30" style="width: 90px;" />
        </a-form-item>
        <a-form-item>
          <a-space>
            <a-button type="primary" :loading="loading" @click="onSearch">分析</a-button>
            <a-button @click="onReset">重置</a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-card>

    <a-card :bordered="false" style="margin-top: 12px;" title="按上榜原因聚合(净买入 / 上榜次数 / 平均评分 / 平均 T+5 收益)">
      <a-empty v-if="!loading && reasonAgg.length === 0" description="无数据" />
      <div v-else ref="chartRef" style="width: 100%; height: 380px;"></div>
      <div class="legend-hint" v-if="reasonAgg.length > 0">
        <span class="dot" :style="{ background: PNL_COLORS.netPos }"></span>净买入(正)
        <span class="dot" :style="{ background: PNL_COLORS.netNeg }"></span>净买入(负)
        <span class="dot" :style="{ background: PNL_COLORS.count }"></span>上榜次数
        <span class="dot" :style="{ background: PNL_COLORS.score }"></span>平均评分
        <span class="dot" :style="{ background: '#13c2c2' }"></span>平均 T+5 (%)
      </div>

      <!-- P16.5: 原因维度 TN 明细表 -->
      <a-table
        v-if="reasonAgg.length > 0"
        style="margin-top: 12px;"
        :data-source="reasonAgg"
        :columns="reasonTnColumns"
        :pagination="false"
        :row-key="(r: ReasonAgg) => r.reason"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'avg_t1' || column.key === 'avg_t5'">
            <span
              v-if="record[column.key] != null"
              :style="{ color: (record[column.key] ?? 0) >= 0 ? '#d4380d' : '#389e0d', fontFamily: 'SF Mono, Menlo, monospace' }"
            >
              {{ (record[column.key] * 100).toFixed(2) }}%
            </span>
            <span v-else style="color: rgba(0,0,0,.25)">-</span>
          </template>
          <template v-else-if="column.key === 't5_win_rate'">
            <span v-if="record.t5_win_rate != null">
              {{ (record.t5_win_rate * 100).toFixed(1) }}%
            </span>
            <span v-else style="color: rgba(0,0,0,.25)">-</span>
          </template>
          <template v-else-if="column.key === 'avg_score'">
            {{ record.avg_score != null ? record.avg_score.toFixed(1) : '-' }}
          </template>
        </template>
      </a-table>
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
              <template v-else-if="['net_sum','buy_sum','sell_sum','avg_net'].includes(column.key)">
                <span :style="['net_sum','avg_net'].includes(column.key) ? { color: record[column.key] >= 0 ? '#d4380d' : '#389e0d' } : {}">
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
              <template v-else-if="['net_sum','buy_sum','sell_sum','avg_net'].includes(column.key)">
                <span :style="['net_sum','avg_net'].includes(column.key) ? { color: record[column.key] >= 0 ? '#d4380d' : '#389e0d' } : {}">
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
import DateRangeQuickPick from '@/components/DateRangeQuickPick.vue'
import { defaultRange, toYmd, type DateRange } from '@/utils/date_range'
import {
  aggregateByReason, aggregateByStock,
  PNL_COLORS,
  type ReasonAgg, type StockAgg,
} from '@/utils/pnl_agg'

echarts.use([BarChart, GridComponent, LegendComponent, TooltipComponent, TitleComponent, CanvasRenderer])

interface FormState {
  range: DateRange   // P19.6c
  topN: number
}
const form = reactive<FormState>({ range: defaultRange(), topN: 10 })

const loading = ref(false)
const rows = ref<LhbRow[]>([])

// P16.5: 原因维度 TN 明细表 columns
const reasonTnColumns = [
  {
    title: '原因', dataIndex: 'reason', key: 'reason', ellipsis: true,
    sorter: (a: ReasonAgg, b: ReasonAgg) => a.reason.localeCompare(b.reason),
  },
  {
    title: '上榜次数', dataIndex: 'count', key: 'count', width: 100, align: 'right' as const,
    sorter: (a: ReasonAgg, b: ReasonAgg) => a.count - b.count,
    defaultSortOrder: 'descend' as const,
  },
  {
    title: '平均评分', dataIndex: 'avg_score', key: 'avg_score', width: 100, align: 'right' as const,
    sorter: (a: ReasonAgg, b: ReasonAgg) => (a.avg_score ?? -1) - (b.avg_score ?? -1),
  },
  {
    title: '平均 T+1', dataIndex: 'avg_t1', key: 'avg_t1', width: 100, align: 'right' as const,
    sorter: (a: ReasonAgg, b: ReasonAgg) => (a.avg_t1 ?? -999) - (b.avg_t1 ?? -999),
  },
  {
    title: '平均 T+5', dataIndex: 'avg_t5', key: 'avg_t5', width: 100, align: 'right' as const,
    sorter: (a: ReasonAgg, b: ReasonAgg) => (a.avg_t5 ?? -999) - (b.avg_t5 ?? -999),
  },
  {
    title: 'T+5 胜率', dataIndex: 't5_win_rate', key: 't5_win_rate', width: 100, align: 'right' as const,
    sorter: (a: ReasonAgg, b: ReasonAgg) => (a.t5_win_rate ?? -1) - (b.t5_win_rate ?? -1),
  },
]

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
    title: '平均净买入', dataIndex: 'avg_net', key: 'avg_net', align: 'right' as const,
    sorter: (a: StockAgg, b: StockAgg) => a.avg_net - b.avg_net,
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
      date_from: form.range.start ? toYmd(form.range.start) : undefined,  // P19.6c
      date_to: form.range.end ? toYmd(form.range.end) : undefined,
      order_by: 'trade_date',
      order_desc: true,
      limit: 1000,
      with_tn: true,
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
  form.range = defaultRange()  // P19.6c
  form.topN = 10
  load()
}

const reasonAgg = computed<ReasonAgg[]>(() => aggregateByReason(rows.value, 10))
const stockAgg = computed<StockAgg[]>(() => aggregateByStock(rows.value))

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
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      valueFormatter: (v: number) => (typeof v === 'number' ? v.toFixed(2) : String(v)),
    },
    legend: {
      data: [
        { name: '净买入(亿)', itemStyle: { color: PNL_COLORS.netPos } },
        { name: '上榜次数', itemStyle: { color: PNL_COLORS.count } },
        { name: '平均评分', itemStyle: { color: PNL_COLORS.score } },
        { name: '平均 T+5 (%)', itemStyle: { color: '#13c2c2' } },
      ],
      top: 0,
    },
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
          color: (p: { data: number }) => (p.data >= 0 ? PNL_COLORS.netPos : PNL_COLORS.netNeg),
        },
      },
      {
        name: '上榜次数', type: 'bar', yAxisIndex: 1,
        data: data.map((d) => d.count),
        itemStyle: { color: PNL_COLORS.count },
      },
      {
        name: '平均评分', type: 'bar', yAxisIndex: 1,
        data: data.map((d) => (d.avg_score != null ? +d.avg_score.toFixed(1) : 0)),
        itemStyle: { color: PNL_COLORS.score },
      },
      {
        name: '平均 T+5 (%)', type: 'bar', yAxisIndex: 1,
        data: data.map((d) => (d.avg_t5 != null ? +(d.avg_t5 * 100).toFixed(2) : 0)),
        itemStyle: { color: '#13c2c2' },
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
.legend-hint {
  margin-top: 8px;
  font-size: 12px;
  color: rgba(0,0,0,0.55);
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.legend-hint .dot {
  display: inline-block;
  width: 10px; height: 10px;
  border-radius: 2px;
  margin: 0 4px 0 12px;
}
.legend-hint .dot:first-child { margin-left: 0; }
</style>
