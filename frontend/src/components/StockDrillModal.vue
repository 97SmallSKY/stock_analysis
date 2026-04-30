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

    <!-- P19.2/P19.3 (2026-04-20): 快捷时间范围按钮(仅日 K / 周 K 显示) -->
    <div v-if="tab !== 'minute'" class="drill-range-bar">
      <a-space :size="6" wrap>
        <a-button
          v-for="opt in RANGE_OPTIONS"
          :key="opt.key"
          size="small"
          :type="opt.key === rangeKey ? 'primary' : 'default'"
          :disabled="loading"
          @click="applyRange(opt.key)"
        >
          {{ opt.label }}
        </a-button>
        <span class="range-info">
          {{ dateFrom || '—' }} ~ {{ dateTo || '—' }}
          <span v-if="drRange" class="range-hint">
            (本股数据 {{ drRange.min }} ~ {{ drRange.max }})
          </span>
        </span>
      </a-space>
    </div>

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

    <!-- P20.7 (2026-04-20): loading 提示细化,带上区间/tab 信息,
         让用户知道在等什么,而不是「是不是卡死了」 -->
    <a-spin :spinning="loading" :tip="loadingTip">
      <div v-if="emptyReason && tab === 'minute'" class="empty-box minute-empty" data-test="minute-empty">
        <div class="big-none">无</div>
        <div class="big-none-sub">{{ emptyTitle }}</div>
        <div class="empty-sub">{{ emptyText }}</div>
        <a-button v-if="emptyReason==='out_of_range'" size="small" @click="tab='daily';load()">返回日K</a-button>
      </div>
      <div v-else-if="emptyReason" class="empty-box">
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
  fetchKlineDaily, fetchKlineWeekly, fetchKlineMinute, fetchKlineDaterange,
  type KlineBar, type KlineDaterange,
} from '@/api/kline'
import { addDays, addMonths, toYmd } from '@/utils/date_range'
import { formatPrice } from '@/utils/format'

const simpleImage = Empty.PRESENTED_IMAGE_SIMPLE

// P19.3 (2026-04-20): K 线快捷范围按钮
type RangeKey = '1w' | '5d' | '2w' | '1m' | '3m' | '6m' | '1y' | 'latest'
const RANGE_OPTIONS: { key: RangeKey; label: string }[] = [
  { key: '1y', label: '1 年' },
  { key: '6m', label: '6 个月' },
  { key: '3m', label: '3 个月' },
  { key: '1m', label: '1 个月' },
  { key: '2w', label: '2 周' },
  { key: '1w', label: '1 周' },
  { key: '5d', label: '5 日' },
  { key: 'latest', label: '最新' },
]

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

// P19.2: 本股在当前频率下的可用日期范围 (后端 /api/kline/daterange)
const drRange = ref<KlineDaterange | null>(null)
// 当前拉取的日期区间(YYYY-MM-DD)
const dateFrom = ref<string | null>(null)
const dateTo = ref<string | null>(null)
// 当前应用的快捷钮 key(默认 6 个月)
const rangeKey = ref<RangeKey>('6m')

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

// P20.7 (2026-04-20): loading 提示文案,根据当前 tab + range 动态生成
const RANGE_LABEL_OF: Record<string, string> = {
  latest: '最新', '5d': '最近 5 日', '1w': '最近 1 周', '2w': '最近 2 周',
  '1m': '最近 1 个月', '3m': '最近 3 个月', '6m': '最近 6 个月',
  '1y': '最近 1 年',
}
const loadingTip = computed(() => {
  const freq = tab.value === 'weekly' ? '周 K'
    : tab.value === 'minute' ? '分钟 K' : '日 K'
  if (tab.value === 'minute') return `拉取 ${freq} · ${pickedDate.value ?? ''}`
  const label = RANGE_LABEL_OF[rangeKey.value] ?? rangeKey.value
  return `拉取 ${freq} · ${label}(${dateFrom.value ?? ''} ~ ${dateTo.value ?? ''})`
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

/**
 * P19.2 (2026-04-20): 根据 range key + daterange 计算 [from, to] ISO 日期。
 * 锚定 = 本股最新有数据的那一天(drRange.max),而不是"今天"。
 * 原因:停牌/次新股,drRange.max 可能比今天早很多,此时往回推最真实。
 *
 * P19.12 (2026-04-20): 可传 anchorOverride。从决策信号/龙虎榜点进来时,
 * 希望默认视图是**该行日期为中心**(前后各半),而不是看最新的 6 个月。
 */
function computeDateRange(
  key: RangeKey,
  dr: KlineDaterange | null,
  anchorOverride?: string | null,
): { from: string; to: string } | null {
  if (!dr || !dr.max) return null
  // P19.12: 若有 anchorOverride,用它作为「中点」,前后各半
  if (anchorOverride) {
    const halfDays = halfDaysOf(key)
    let from = addDays(anchorOverride, -halfDays)
    let to = addDays(anchorOverride, halfDays)
    if (dr.min && from < dr.min) from = dr.min
    if (dr.max && to > dr.max) to = dr.max
    return { from, to }
  }
  const anchor = dr.max  // YYYY-MM-DD
  let from: string
  switch (key) {
    case 'latest':
      // 最新 = 最近 60 个自然日 (≈ 40 交易日)
      from = addDays(anchor, -60)
      break
    case '5d':  from = addDays(anchor, -7); break      // 约 5 交易日
    case '1w':  from = addDays(anchor, -10); break     // 约 1 周
    case '2w':  from = addDays(anchor, -20); break
    case '1m':  from = addMonths(anchor, -1); break
    case '3m':  from = addMonths(anchor, -3); break
    case '6m':  from = addMonths(anchor, -6); break
    case '1y':  from = addMonths(anchor, -12); break
  }
  // 不能早于 dr.min
  if (dr.min && from < dr.min) from = dr.min
  return { from, to: anchor }
}

/** P19.12: 对每个 rangeKey 返回「半宽天数」,供 anchor 居中展示用。 */
function halfDaysOf(key: RangeKey): number {
  switch (key) {
    case 'latest': return 30     // 共 60 天
    case '5d':     return 4
    case '1w':     return 5
    case '2w':     return 10
    case '1m':     return 15
    case '3m':     return 45
    case '6m':     return 90
    case '1y':     return 180
  }
}

function applyRange(key: RangeKey) {
  rangeKey.value = key
  // P19.12: 手动点快捷钮时清除 anchor,回归"最新日期锚定"语义
  // (anchor 只在初次打开时一次性生效,用户拖日期后就不再使用)
  const r = computeDateRange(key, drRange.value)
  if (r) {
    dateFrom.value = r.from
    dateTo.value = r.to
  }
  load()
}

async function ensureDaterange() {
  // P19.2: 打开/切股票 时调一次 daterange,缓存在 drRange
  if (!drill.state.tsCode) return
  if (tab.value === 'minute') return  // 分钟 K 用 pickedDate,不用 daterange
  const freq = tab.value
  try {
    drRange.value = await fetchKlineDaterange(drill.state.tsCode, freq)
  } catch {
    drRange.value = null
  }
  // P19.12: 如果有 anchorDate(从 Signals/LhbList 跳入),第一次渲染用它为中点;
  // 用户随后点快捷钮/手动选日期后,anchor 被清空(见 applyRange)
  const anchor = drill.state.anchorDate
  const r = computeDateRange(rangeKey.value, drRange.value, anchor)
  if (r) {
    dateFrom.value = r.from
    dateTo.value = r.to
  } else {
    dateFrom.value = null
    dateTo.value = null
  }
}

async function load() {
  if (!drill.state.tsCode) return
  loading.value = true
  rows.value = []
  emptyReason.value = null
  try {
    // 先确保 daterange 已拿(或已为本次股票+频率计算过)
    if (tab.value !== 'minute' && !drRange.value) {
      await ensureDaterange()
    }
    let body
    if (tab.value === 'daily') {
      // P19.2: 带 from/to 参数,默认 6 个月区间
      const from = dateFrom.value ? toYmd(dateFrom.value) : undefined
      const to = dateTo.value ? toYmd(dateTo.value) : undefined
      body = await fetchKlineDaily(drill.state.tsCode, from, to, 2000)
    } else if (tab.value === 'weekly') {
      const from = dateFrom.value ? toYmd(dateFrom.value) : undefined
      const to = dateTo.value ? toYmd(dateTo.value) : undefined
      body = await fetchKlineWeekly(drill.state.tsCode, from, to, 500)
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

  // P19.4 (2026-04-20): 区间内最高价 / 最低价标注
  // 找到 close 非空的最大/最小点
  let maxIdx = -1, minIdx = -1
  let maxVal = -Infinity, minVal = Infinity
  for (let i = 0; i < rows.value.length; i++) {
    const h = rows.value[i].high ?? null
    const l = rows.value[i].low ?? null
    if (h != null && h > maxVal) { maxVal = h; maxIdx = i }
    if (l != null && l < minVal) { minVal = l; minIdx = i }
  }
  // STO-30: 价格精度自适应。maxVal 作为整档决策基准,避免 Y 轴精度跳动。
  //   maxVal < 10 → 3 位(低价股/北交所/ST)
  //   10~100 → 2 位(主流)
  //   >= 100 → 1 位(高价股)
  // STO-65 (2026-04-25): markPoint 从「pin 圆圈 + 白字」改为「纯文字」
  //   - symbol: 'none' 移除圆圈底纹
  //   - label.color 换成高饱和红/绿直接显示在 K 线旁
  const markPoints: { name: string; coord: [number, number]; value: string; symbol: string; label: Record<string, unknown> }[] = []
  if (maxIdx >= 0) {
    markPoints.push({
      name: '区间最高',
      coord: [maxIdx, maxVal],
      value: String(maxVal),
      symbol: 'none',
      label: {
        show: true, position: 'top', color: '#d4380d',
        fontSize: 12, fontWeight: 'bold',
        formatter: `▲ ${formatPrice(maxVal, maxVal)}`,
      },
    })
  }
  if (minIdx >= 0 && minIdx !== maxIdx) {
    markPoints.push({
      name: '区间最低',
      coord: [minIdx, minVal],
      value: String(minVal),
      symbol: 'none',
      label: {
        show: true, position: 'bottom', color: '#389e0d',
        fontSize: 12, fontWeight: 'bold',
        formatter: `▼ ${formatPrice(minVal, maxVal)}`,
      },
    })
  }

  // STO-30: Y 轴 5% padding,避免 markPoint pin 出框 / 蜡烛贴边
  const yRange = maxVal - minVal
  const yPad = yRange > 0 ? yRange * 0.05 : Math.max(maxVal * 0.05, 0.01)
  // 按价格档位决定 round 精度(和 formatPrice 对齐)
  const roundStep = maxVal < 10 ? 1000 : maxVal < 100 ? 100 : 10
  const yMin = isFinite(minVal) ? Math.floor((minVal - yPad) * roundStep) / roundStep : null
  const yMax = isFinite(maxVal) ? Math.ceil((maxVal + yPad) * roundStep) / roundStep : null

  const series: Record<string, unknown>[] = [
    {
      name: 'K 线', type: 'candlestick', data: kdata,
      itemStyle: {
        color: '#d4380d', color0: '#389e0d',
        borderColor: '#d4380d', borderColor0: '#389e0d',
      },
      // P19.4: 标注最高/最低
      // STO-65 (2026-04-25): 由 pin/symbolSize=48 改为纯文字标注,symbol/size 由各 markPoint 自带覆盖
      markPoint: {
        data: markPoints,
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
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      // STO-65 (2026-04-25): 自定义中文 OHLC + 涨跌幅,跟随鼠标移动
      // K 线 series.data 顺序: [open, close, low, high]
      formatter: (params: unknown) => {
        const arr = Array.isArray(params) ? params : [params]
        const k = arr.find((p: { seriesType?: string }) => p?.seriesType === 'candlestick')
        if (!k) {
          // 退化 (例: 用户切到 minute 没有 candlestick),回退到默认行为
          return arr.map((p: { seriesName?: string; value?: unknown }) => {
            const v = typeof p.value === 'number' ? formatPrice(p.value, maxVal) : String(p.value ?? '-')
            return `${p.seriesName ?? ''} ${v}`
          }).join('<br/>')
        }
        const k1 = k as { axisValueLabel?: string; data?: unknown[] }
        const date = k1.axisValueLabel ?? ''
        const data = (k1.data ?? []) as unknown[]
        // echarts 蜡烛图 data 是 [idx, open, close, low, high] 或者 [open, close, low, high]
        // 真实回调里 data 形如 [idx, open, close, low, high]
        const offset = data.length >= 5 ? 1 : 0
        const open = Number(data[offset] ?? 0)
        const close = Number(data[offset + 1] ?? 0)
        const low = Number(data[offset + 2] ?? 0)
        const high = Number(data[offset + 3] ?? 0)
        const pctChg = open !== 0 ? ((close - open) / open) * 100 : 0
        const color = pctChg >= 0 ? '#d4380d' : '#389e0d'
        const sign = pctChg >= 0 ? '+' : ''
        const fp = (v: number) => formatPrice(v, maxVal)
        const lines = [
          `<div style="font-weight:600;margin-bottom:4px;">${date}</div>`,
          `<div>开盘: <b>${fp(open)}</b></div>`,
          `<div>收盘: <b>${fp(close)}</b></div>`,
          `<div>最高: <b style="color:#d4380d;">${fp(high)}</b></div>`,
          `<div>最低: <b style="color:#389e0d;">${fp(low)}</b></div>`,
          `<div>涨跌幅: <b style="color:${color};">${sign}${pctChg.toFixed(2)}%</b></div>`,
        ]
        // 附 MA5/MA10(若 legend 选中)
        for (const p of arr) {
          const pp = p as { seriesName?: string; seriesType?: string; value?: unknown }
          if (pp.seriesType === 'line' && typeof pp.value === 'number') {
            lines.push(`<div>${pp.seriesName}: ${fp(pp.value as number)}</div>`)
          }
        }
        return lines.join('')
      },
    },
    // STO-114: MA5/MA10 默认展示(STO-30 隐藏是用户反馈的 bug,改回可见)
    legend: {
      data: series.map((s) => s.name as string),
      top: 0,
      selected: { MA5: true, MA10: true },
    },
    grid: { left: 50, right: 30, top: 40, bottom: 60 },
    xAxis: { type: 'category', data: dates, boundaryGap: true, axisLine: { onZero: false } },
    yAxis: {
      scale: true,
      splitLine: { show: true },
      // STO-30: 两端 5% padding + 按档位精度 round(避免 Y 轴刻度奇怪的 1.237 这种)
      ...(yMin !== null && yMax !== null ? { min: yMin, max: yMax } : {}),
      // STO-30: axisLabel 也用 formatPrice,Y 轴刻度和价格精度对齐
      axisLabel: {
        formatter: (v: number) => formatPrice(v, maxVal),
      },
    },
    // P19.3: 数据已经按快捷钮精确限定范围,dataZoom 全显一目了然
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', bottom: 10, height: 18, start: 0, end: 100 },
    ],
    series,
  })

  // 点击 K 线 bar -> 记录日期
  // P19.12 (2026-04-20): 修复「日 K 点不动」问题。
  //   旧实现:点日 K 蜡烛 → 自动切周 K,用户感觉「点击没响应」(其实图表变了但看不出)
  //   新实现:
  //     - 日 K:仅锁定 pickedDate + 显示顶部 badge,不切 tab。用户看得到反馈。
  //     - 周 K:仍切到分钟 K(必须先选日期)
  chart.off('click')
  chart.on('click', (ev: { seriesType?: string; dataIndex?: number }) => {
    if (ev.seriesType !== 'candlestick') return
    const idx = ev.dataIndex
    if (idx == null) return
    const d = dates[idx]
    if (!d) return
    pickedDate.value = d
    if (tab.value === 'weekly') {
      tab.value = 'minute'
    }
    // daily 不切 tab,保持视图
  })
}

async function onTabChange() {
  // P19.2: 切 tab 时(日 K / 周 K)重新拉 daterange
  if (tab.value !== 'minute') {
    drRange.value = null
    await ensureDaterange()
  }
  load()
}

function onResize() { chart?.resize() }
window.addEventListener('resize', onResize)

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose(); chart = null
})

// 打开时 / 切股票时 重置并加载
watch(
  () => [drill.state.open, drill.state.tsCode],
  async ([open]) => {
    if (!open) return
    tab.value = 'daily'
    pickedDate.value = null
    // P19.2: 默认 6 个月,daterange 会由 ensureDaterange 拉取
    rangeKey.value = '6m'
    drRange.value = null
    dateFrom.value = null
    dateTo.value = null
    await ensureDaterange()
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
.drill-range-bar {
  margin: -4px 0 8px;
  padding: 8px 12px;
  background: #fafbfc;
  border-radius: 6px;
}
.drill-range-bar .range-info {
  margin-left: 12px;
  font-size: 12px;
  color: #595959;
  font-family: 'SF Mono', Menlo, monospace;
}
.drill-range-bar .range-hint {
  margin-left: 6px;
  color: #bfbfbf;
  font-size: 11px;
}
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
.minute-empty { text-align: center; }
.big-none {
  font-size: 120px;
  font-weight: 200;
  color: #bfbfbf;
  line-height: 1;
  letter-spacing: 8px;
  margin-bottom: 12px;
  user-select: none;
}
.big-none-sub {
  font-size: 14px; color: #595959; font-weight: 500; margin-bottom: 6px;
}
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
