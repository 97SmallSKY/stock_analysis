<template>
  <a-modal
    v-model:open="drill.state.open"
    width="960px"
    :footer="null"
    :destroy-on-close="true"
    :body-style="{ padding: '16px 24px 20px' }"
    @cancel="drill.close()"
  >
    <template #title>
      <div class="drill-title">
        <AreaChartOutlined style="color:#d4380d;" />
        <span class="ts">{{ drill.state.tsCode ?? '' }}</span>
        <span class="freq">· {{ freqLabel }}</span>
      </div>
    </template>

    <a-tabs v-model:active-key="tab" @change="onTabChange" class="drill-tabs">
      <a-tab-pane key="daily" tab="日 K" />
      <a-tab-pane key="weekly" tab="周 K · MA5" />
      <a-tab-pane
        key="minute"
        :tab="minuteTabLabel"
        :disabled="!pickedDate"
      />
    </a-tabs>

    <a-breadcrumb v-if="tab !== 'daily'" class="drill-bread" separator="→">
      <a-breadcrumb-item>
        <a @click="tab='daily'">日 K</a>
      </a-breadcrumb-item>
      <a-breadcrumb-item>
        <a v-if="tab === 'minute'" @click="tab='weekly'">周 K</a>
        <span v-else>周 K</span>
      </a-breadcrumb-item>
      <a-breadcrumb-item v-if="tab === 'minute'">
        分钟 K · {{ pickedDate }}
      </a-breadcrumb-item>
    </a-breadcrumb>

    <div v-if="pickedDate && tab !== 'daily'" class="bread">
      <a-tag color="orange">
        <ClockCircleOutlined /> 选中日期: {{ pickedDate }}
      </a-tag>
      <a-button type="link" size="small" @click="resetPicked">清除选择</a-button>
    </div>

    <a-spin :spinning="loading" tip="拉取行情中...">
      <div v-if="emptyReason" class="empty-box">
        <a-empty :image="simpleImage" :description="null">
          <template #description>
            <div class="empty-title">{{ emptyTitle }}</div>
            <div class="empty-sub">{{ emptyText }}</div>
          </template>
          <a-button v-if="emptyReason==='out_of_range'" size="small" @click="tab='daily';load()">返回日K</a-button>
        </a-empty>
      </div>
      <div v-else ref="chartRef" class="chart-box"></div>
    </a-spin>

    <div class="drill-hint">
      <InfoCircleOutlined /> 点日 K 的某根蜡烛 → 切换到周 K;再点周 K → 切换到分钟 K
    </div>
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
import { Empty } from 'ant-design-vue'
import {
  AreaChartOutlined, ClockCircleOutlined, InfoCircleOutlined,
} from '@ant-design/icons-vue'
import { useStockDrill } from '@/composables/useStockDrill'
import {
  fetchKlineDaily, fetchKlineWeekly, fetchKlineMinute, type KlineBar,
} from '@/api/kline'

const simpleImage = Empty.PRESENTED_IMAGE_SIMPLE

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

const freqLabel = computed(() => {
  if (tab.value === 'daily') return '日 K 线'
  if (tab.value === 'weekly') return '周 K 线 · MA5'
  return '分钟 K'
})

const minuteTabLabel = computed(() =>
  pickedDate.value ? `分钟 K · ${pickedDate.value}` : '分钟 K (需先选日期)',
)

const emptyTitle = computed(() => {
  if (emptyReason.value === 'no_source_table') return '数据源尚未接入'
  if (emptyReason.value === 'out_of_range') return '暂无行情数据'
  if (emptyReason.value === 'no_pick') return '请先选择日期'
  if (emptyReason.value === 'fetch_failed') return '网络或服务异常'
  return ''
})

const emptyText = computed(() => {
  if (!emptyReason.value) return ''
  if (emptyReason.value === 'no_source_table')
    return '分钟级 K 线暂未接入数据源 (Tushare stk_mins / AkShare stock_zh_a_minute),接口契约已就位,接入后无需前端变更'
  if (emptyReason.value === 'out_of_range') return '所选区间内无行情,请调整时间范围'
  if (emptyReason.value === 'no_pick') return '点击日 K 蜡烛自动选择某日期,再切到分钟 K'
  if (emptyReason.value === 'fetch_failed') return '请稍后重试'
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
.drill-title {
  display: flex; align-items: center; gap: 8px;
  font-weight: 500;
}
.drill-title .ts {
  font-family: 'SF Mono', Menlo, monospace;
  font-size: 16px; letter-spacing: 0.3px;
}
.drill-title .freq {
  color: #8c8c8c; font-size: 13px; font-weight: 400;
}
.drill-tabs { margin-top: -4px; }
.drill-bread {
  margin: -4px 0 8px;
  padding: 6px 12px;
  background: linear-gradient(90deg, #fff7f5 0%, #fff 100%);
  border-radius: 6px;
  font-size: 12px;
}
.bread { margin-bottom: 8px; }
.chart-box {
  width: 100%; height: 480px;
  border-radius: 6px; overflow: hidden;
}
.empty-box { padding: 80px 0; }
.empty-title {
  font-size: 14px; color: #595959; font-weight: 500; margin-bottom: 4px;
}
.empty-sub {
  font-size: 12px; color: #bfbfbf; max-width: 420px; margin: 0 auto;
  line-height: 1.6;
}
.drill-hint {
  margin-top: 8px;
  color: #bfbfbf; font-size: 12px;
  display: flex; align-items: center; gap: 6px;
}
</style>
