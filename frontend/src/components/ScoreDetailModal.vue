<template>
  <!-- STO-35: 从 Signals.vue 提取的评分明细 Modal(P19.8 / P19.10 / P19.12 原逻辑)。
       用法:
       <ScoreDetailModal v-model:open="detailOpen" :row="detailRow" />
       LhbPnl / Signals 共用,保证雷达图 + 3 列分类明细的样式一致。 -->
  <a-modal
    :open="open"
    :title="title"
    width="860px"
    :footer="null"
    destroy-on-close
    @update:open="(v: boolean) => emit('update:open', v)"
    @close="onClose"
  >
    <div v-if="row" class="detail-modal">
      <a-descriptions :column="3" size="small" bordered class="detail-header">
        <a-descriptions-item label="股票">
          <StockLink :code="row.ts_code" :name="row.name" :anchor-date="row.trade_date" />
        </a-descriptions-item>
        <a-descriptions-item label="日期">{{ fmtDate(row.trade_date) }}</a-descriptions-item>
        <a-descriptions-item label="综合评分">
          <a-tag :color="scoreColor(row.score)">{{ row.score ?? '-' }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="净买入" :span="1">{{ fmtAmountWan(row.net_amount) }}</a-descriptions-item>
        <a-descriptions-item label="上榜原因" :span="2">{{ row.reason }}</a-descriptions-item>
      </a-descriptions>

      <div class="detail-body">
        <div class="detail-radar-col">
          <div ref="radarEl" class="detail-radar"></div>
          <div class="detail-radar-hint">
            <div>每维得分已归一到 0~100。</div>
            <div v-if="weightsInfo">
              综合评分 = 资金×{{ weightsInfo.fund }} + 历史×{{ weightsInfo.hist }}
              + 技术×{{ weightsInfo.tech }}
              <span v-if="weightsInfo.sentiment != null">× 情绪系数 {{ weightsInfo.sentiment }}</span>
            </div>
            <div>同一只强势股多维满分属正常。</div>
          </div>
        </div>
        <div class="detail-dim-col">
          <div v-for="g in dimGroups" :key="g.key" class="dim-group">
            <div class="dim-title" :style="{ borderLeftColor: g.color }">{{ g.title }}</div>
            <div v-for="item in g.items" :key="item.k" class="dim-row">
              <span class="dim-name">{{ item.label }}</span>
              <span class="dim-value" :style="{ color: dimColor(item.v) }">
                {{ formatDimValue(item.v) }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { LhbRow } from '@/api/lhb'
import { fmtAmountWan, fmtDate } from '@/utils/format'
import StockLink from '@/components/StockLink.vue'
import { extractRadarData, labelOf } from '@/utils/score_detail'

import * as echarts from 'echarts/core'
import { RadarChart } from 'echarts/charts'
import { TooltipComponent, TitleComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
echarts.use([RadarChart, TooltipComponent, TitleComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{
  open: boolean
  row: LhbRow | null
}>()

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
}>()

const radarEl = ref<HTMLDivElement | null>(null)
let radarChart: echarts.ECharts | null = null

const title = computed(() => {
  if (!props.row) return '评分明细'
  const r = props.row
  const name = r.name ? ` ${r.name}` : ''
  return `评分明细 · ${r.ts_code}${name} · ${fmtDate(r.trade_date)}`
})

function scoreColor(s: number | null) {
  if (s == null) return 'default'
  if (s >= 85) return 'red'
  if (s >= 70) return 'orange'
  return 'default'
}

interface DimItem { k: string; label: string; v: unknown }
interface DimGroup { key: string; title: string; color: string; items: DimItem[] }

const dimGroups = computed<DimGroup[]>(() => {
  const d = props.row?.score_detail as Record<string, unknown> | null | undefined
  if (!d) return []
  const meta = [
    { key: 'fund', title: '资金维度', color: '#1677ff' },
    { key: 'hist', title: '历史表现', color: '#faad14' },
    { key: 'tech', title: '技术面',   color: '#52c41a' },
  ]
  const out: DimGroup[] = []
  for (const m of meta) {
    const sub = d[m.key] as Record<string, unknown> | undefined
    if (!sub || typeof sub !== 'object') continue
    const items: DimItem[] = []
    for (const [k, v] of Object.entries(sub)) {
      items.push({ k, label: labelOf(k), v })
    }
    out.push({ key: m.key, title: m.title, color: m.color, items })
  }
  return out
})

const weightsInfo = computed<{ fund: string; hist: string; tech: string; sentiment: string | null } | null>(() => {
  const d = props.row?.score_detail as Record<string, unknown> | null | undefined
  if (!d) return null
  const w = d.weights as Record<string, number> | undefined
  const coef = d.sentiment_coef
  if (!w) return null
  return {
    fund: String(w.fund ?? '-'),
    hist: String(w.hist ?? '-'),
    tech: String(w.tech ?? '-'),
    sentiment: typeof coef === 'number' ? coef.toFixed(2) : null,
  }
})

function formatDimValue(v: unknown): string {
  if (v == null) return '-'
  if (typeof v === 'number') {
    return Number.isInteger(v) ? String(v) : v.toFixed(2)
  }
  return String(v)
}

function dimColor(v: unknown): string {
  if (typeof v !== 'number') return '#262626'
  if (v >= 80) return '#d4380d'
  if (v >= 50) return '#faad14'
  if (v > 0)   return '#52c41a'
  return '#8c8c8c'
}

function renderRadar(row: LhbRow) {
  if (!radarEl.value) return
  const rd = extractRadarData(row.score_detail as Record<string, unknown> | null)
  if (!rd.indicator.length) return

  radarChart?.dispose()
  radarChart = echarts.init(radarEl.value)
  radarChart.setOption({
    tooltip: { trigger: 'item' },
    legend: { data: ['评分维度'], top: 8 },
    radar: {
      indicator: rd.indicator,
      splitNumber: 4,
      axisName: { color: '#595959', fontSize: 11 },
      splitLine: { lineStyle: { color: '#e7e7e7' } },
      splitArea: { areaStyle: { color: ['#fafafa', '#fff'] } },
    },
    series: [
      {
        name: '评分维度',
        type: 'radar',
        data: [
          {
            name: '当前股票',
            value: rd.value,
            areaStyle: { color: 'rgba(212, 56, 13, 0.25)' },
            lineStyle: { color: '#d4380d', width: 2 },
            itemStyle: { color: '#d4380d' },
          },
        ],
      },
    ],
  })
  const onResize = () => radarChart?.resize()
  window.addEventListener('resize', onResize, { once: true })
}

function onClose() {
  if (radarChart) {
    radarChart.dispose()
    radarChart = null
  }
}

watch(
  () => [props.open, props.row?.ts_code, props.row?.trade_date],
  ([open]) => {
    if (open && props.row) {
      nextTick(() => renderRadar(props.row!))
    }
  },
)
</script>

<style scoped>
.detail-modal { display: flex; flex-direction: column; gap: 12px; }
.detail-header :deep(.ant-descriptions-item-label) { font-weight: 500; color: #595959; }
.detail-body { display: flex; gap: 16px; align-items: flex-start; }
.detail-radar-col { flex: 0 0 380px; display: flex; flex-direction: column; gap: 8px; }
.detail-radar { width: 380px; height: 280px; }
.detail-radar-hint { font-size: 11px; color: #8c8c8c; line-height: 1.6; padding: 0 8px; }
.detail-dim-col { flex: 1; display: flex; flex-direction: column; gap: 10px; }
.dim-group { border: 1px solid #f0f0f0; border-radius: 6px; padding: 8px 12px; background: #fafafa; }
.dim-title { font-weight: 500; color: #262626; border-left: 3px solid #1677ff; padding-left: 8px; margin-bottom: 6px; }
.dim-row { display: flex; justify-content: space-between; font-size: 12px; line-height: 1.8; padding: 0 2px; }
.dim-name { color: #595959; }
.dim-value { font-family: 'SF Mono', Menlo, monospace; font-weight: 500; }
</style>
