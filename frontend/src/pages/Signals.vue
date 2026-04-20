<template>
  <div class="signals-page">
    <a-card :bordered="false" :body-style="{ paddingBottom: 8 }">
      <a-form layout="inline" :model="form" @submit.prevent="onSearch">
        <a-form-item label="日期">
          <!-- P19.6b (2026-04-20): DateRangeQuickPick 替换 a-range-picker。默认 1 年。 -->
          <DateRangeQuickPick v-model="form.range" />
        </a-form-item>
        <a-form-item label="评分≥">
          <a-slider
            v-model:value="form.scoreMin"
            :min="0"
            :max="100"
            :step="1"
            style="width: 180px;"
          />
          <span style="margin-left: 8px;">{{ form.scoreMin }}</span>
        </a-form-item>
        <a-form-item label="原因关键字">
          <a-input v-model:value="form.reason" allow-clear placeholder="如 涨停" style="width: 160px;" />
        </a-form-item>
        <a-form-item>
          <a-space>
            <a-button type="primary" :loading="loading" @click="onSearch">查询</a-button>
            <a-button @click="onReset">重置</a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-card>

    <a-row :gutter="12" style="margin-top: 12px;">
      <a-col :span="18">
        <a-card :bordered="false" title="候选信号">
          <a-table
            :data-source="rows"
            :columns="columns"
            :loading="loading"
            :pagination="{ pageSize: 20, showSizeChanger: false }"
            :row-key="rowKey"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'ts_code'">
                <StockLink :code="record.ts_code" :name="record.name" />
              </template>
              <template v-else-if="column.key === 'trade_date'">{{ fmtDate(record.trade_date) }}</template>
              <template v-else-if="column.key === 'net_amount'">
                <span :style="{ color: (record.net_amount ?? 0) >= 0 ? '#d4380d' : '#389e0d' }">
                  {{ fmtAmountWan(record.net_amount) }}
                </span>
              </template>
              <template v-else-if="column.key === 'score'">
                <a-tag :color="scoreColor(record.score)">
                  {{ record.score != null ? Number(record.score).toFixed(1) : '-' }}
                </a-tag>
              </template>
              <template v-else-if="column.key === 't1_pnl_pct' || column.key === 't5_pnl_pct'">
                <span
                  v-if="record[column.key] != null"
                  :style="{ color: (record[column.key] ?? 0) >= 0 ? '#d4380d' : '#389e0d', fontFamily: 'SF Mono, Menlo, monospace' }"
                >
                  {{ ((record[column.key] ?? 0) * 100).toFixed(2) }}%
                </span>
                <span v-else style="color: rgba(0,0,0,.25)">-</span>
              </template>
              <template v-else-if="column.key === 'score_detail'">
                <!-- P19.8 (2026-04-20): 改为打开 Modal,展示雷达图 + 明细。
                     旧的 popover 难以在小空间里同时看五六个维度 -->
                <a-button
                  v-if="record.score_detail"
                  type="link"
                  size="small"
                  @click="openDetailModal(record)"
                >
                  详情
                </a-button>
                <span v-else>-</span>
              </template>
            </template>
          </a-table>
        </a-card>
      </a-col>
      <a-col :span="6">
        <a-card :bordered="false" title="统计">
          <a-descriptions :column="1" size="small" bordered>
            <a-descriptions-item label="信号总数">{{ summary.count }}</a-descriptions-item>
            <a-descriptions-item label="涉及股票">{{ summary.uniqStocks }}</a-descriptions-item>
            <a-descriptions-item label="平均评分">
              {{ summary.avgScore != null ? summary.avgScore.toFixed(2) : '-' }}
            </a-descriptions-item>
            <a-descriptions-item label="净买入合计">{{ fmtAmountWan(summary.netSum) }}</a-descriptions-item>
            <a-descriptions-item label="净买入均值">{{ fmtAmountWan(summary.netAvg) }}</a-descriptions-item>
            <a-descriptions-item label="T+1 平均">
              <span :style="{ color: (tnSummary.t1Avg ?? 0) >= 0 ? '#d4380d' : '#389e0d' }">
                {{ tnSummary.t1Avg != null ? (tnSummary.t1Avg * 100).toFixed(2) + '%' : '-' }}
              </span>
              <span style="color: rgba(0,0,0,.45); margin-left:6px">
                ({{ tnSummary.t1Cnt }} 有效)
              </span>
            </a-descriptions-item>
            <a-descriptions-item label="T+1 胜率">
              {{ tnSummary.t1WinRate != null ? (tnSummary.t1WinRate * 100).toFixed(1) + '%' : '-' }}
            </a-descriptions-item>
            <a-descriptions-item label="T+5 平均">
              <span :style="{ color: (tnSummary.t5Avg ?? 0) >= 0 ? '#d4380d' : '#389e0d' }">
                {{ tnSummary.t5Avg != null ? (tnSummary.t5Avg * 100).toFixed(2) + '%' : '-' }}
              </span>
            </a-descriptions-item>
          </a-descriptions>
          <a-divider style="margin: 12px 0;" />
          <div style="font-weight: 500; margin-bottom: 8px;">前 5 上榜原因</div>
          <a-empty v-if="summary.topReasons.length === 0" :image="simpleImage" description="" />
          <div v-else>
            <div
              v-for="(t, i) in summary.topReasons"
              :key="i"
              style="display:flex; justify-content: space-between; margin-bottom: 4px;"
            >
              <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 70%;">
                {{ t.reason }}
              </span>
              <a-tag>{{ t.count }}</a-tag>
            </div>
          </div>
        </a-card>
      </a-col>
    </a-row>

    <a-row :gutter="12" style="margin-top: 12px;" data-test="dim-cards">
      <a-col :span="8">
        <a-card :bordered="false" title="评分段分布" size="small" class="dim-card">
          <div
            v-for="b in dimScore"
            :key="b.label"
            class="dim-bar"
          >
            <span class="dim-label">{{ b.label }}</span>
            <div class="dim-track">
              <div class="dim-fill score" :style="{ width: pct(b.count, summary.count) + '%' }"></div>
            </div>
            <span class="dim-count">{{ b.count }}</span>
          </div>
        </a-card>
      </a-col>
      <a-col :span="8">
        <a-card :bordered="false" title="净买入区间" size="small" class="dim-card">
          <div
            v-for="b in dimNet"
            :key="b.label"
            class="dim-bar"
          >
            <span class="dim-label">{{ b.label }}</span>
            <div class="dim-track">
              <div class="dim-fill net" :style="{ width: pct(b.count, summary.count) + '%' }"></div>
            </div>
            <span class="dim-count">{{ b.count }}</span>
          </div>
        </a-card>
      </a-col>
      <a-col :span="8">
        <a-card :bordered="false" title="分日数量" size="small" class="dim-card">
          <a-empty v-if="dimDaily.length === 0" :image="simpleImage" description="无" />
          <div v-else class="daily-rows">
            <div
              v-for="b in dimDaily"
              :key="b.date"
              class="dim-bar"
            >
              <span class="dim-label mono">{{ b.date }}</span>
              <div class="dim-track">
                <div class="dim-fill daily" :style="{ width: pct(b.count, dailyMax) + '%' }"></div>
              </div>
              <span class="dim-count">{{ b.count }}</span>
            </div>
          </div>
        </a-card>
      </a-col>
    </a-row>

    <!-- P19.8 (2026-04-20): 评分明细 Modal,雷达图 + 中文标签明细表 -->
    <a-modal
      v-model:open="detailOpen"
      :title="detailTitle"
      width="720px"
      :footer="null"
      destroy-on-close
      @close="onDetailClose"
    >
      <a-row v-if="detailRow" :gutter="16">
        <a-col :span="14">
          <div ref="radarEl" style="width: 100%; height: 360px;"></div>
        </a-col>
        <a-col :span="10">
          <a-descriptions :column="1" size="small" bordered>
            <a-descriptions-item label="股票">
              <StockLink :code="detailRow.ts_code" :name="detailRow.name" />
            </a-descriptions-item>
            <a-descriptions-item label="日期">{{ fmtDate(detailRow.trade_date) }}</a-descriptions-item>
            <a-descriptions-item label="综合评分">
              <a-tag :color="scoreColor(detailRow.score)">{{ detailRow.score ?? '-' }}</a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="净买入">{{ fmtAmountWan(detailRow.net_amount) }}</a-descriptions-item>
            <a-descriptions-item label="上榜原因">{{ detailRow.reason }}</a-descriptions-item>
          </a-descriptions>
          <pre class="score-detail-pre" style="margin-top: 12px;">{{ formatDetail(detailRow.score_detail) }}</pre>
        </a-col>
      </a-row>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, nextTick } from 'vue'
import { Empty } from 'ant-design-vue'
import { fetchLhb, type LhbRow } from '@/api/lhb'
import { fmtAmountWan, fmtDate } from '@/utils/format'
import StockLink from '@/components/StockLink.vue'
import DateRangeQuickPick from '@/components/DateRangeQuickPick.vue'
import { defaultRange, toYmd, type DateRange } from '@/utils/date_range'
import {
  summarize, scoreBuckets, netBuckets, dailyBuckets,
} from '@/utils/signals_agg'
import { tnSummarize } from '@/utils/tn_summary'
import { formatScoreDetail, extractRadarData } from '@/utils/score_detail'

// P19.8: 按需引入 ECharts(雷达图)
import * as echarts from 'echarts/core'
import { RadarChart } from 'echarts/charts'
import { TooltipComponent, TitleComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
echarts.use([RadarChart, TooltipComponent, TitleComponent, LegendComponent, CanvasRenderer])

const simpleImage = Empty.PRESENTED_IMAGE_SIMPLE

interface FormState {
  range: DateRange        // P19.6b: 改用 DateRange
  scoreMin: number
  reason?: string
}
const form = reactive<FormState>({
  range: defaultRange(),  // P19.6b: 默认一年
  scoreMin: 70,
  reason: undefined,
})

const rows = ref<LhbRow[]>([])
const loading = ref(false)

const columns = [
  {
    title: '股票', dataIndex: 'ts_code', key: 'ts_code', width: 180,
    sorter: (a: LhbRow, b: LhbRow) => a.ts_code.localeCompare(b.ts_code),
  },
  {
    title: '日期', key: 'trade_date', dataIndex: 'trade_date', width: 110,
    sorter: (a: LhbRow, b: LhbRow) => (a.trade_date ?? '').localeCompare(b.trade_date ?? ''),
    defaultSortOrder: 'descend' as const,
  },
  {
    title: '上榜原因', dataIndex: 'reason', key: 'reason', ellipsis: true,
    sorter: (a: LhbRow, b: LhbRow) => (a.reason ?? '').localeCompare(b.reason ?? ''),
  },
  {
    title: '净买入', key: 'net_amount', dataIndex: 'net_amount', width: 130, align: 'right' as const,
    sorter: (a: LhbRow, b: LhbRow) => (a.net_amount ?? 0) - (b.net_amount ?? 0),
  },
  {
    title: '评分', key: 'score', dataIndex: 'score', width: 90, align: 'right' as const,
    sorter: (a: LhbRow, b: LhbRow) => (a.score ?? -1) - (b.score ?? -1),
    defaultSortOrder: 'descend' as const,
  },
  // P16.5: T+1 / T+5 次日收益 (lhb_tn_price JOIN)
  {
    title: 'T+1', key: 't1_pnl_pct', dataIndex: 't1_pnl_pct', width: 90, align: 'right' as const,
    sorter: (a: LhbRow, b: LhbRow) => (a.t1_pnl_pct ?? -999) - (b.t1_pnl_pct ?? -999),
  },
  {
    title: 'T+5', key: 't5_pnl_pct', dataIndex: 't5_pnl_pct', width: 90, align: 'right' as const,
    sorter: (a: LhbRow, b: LhbRow) => (a.t5_pnl_pct ?? -999) - (b.t5_pnl_pct ?? -999),
  },
  { title: '评分明细', key: 'score_detail', width: 90 },
]

function rowKey(r: LhbRow) { return `${r.ts_code}_${r.trade_date}` }

function scoreColor(s: number | null) {
  if (s == null) return 'default'
  if (s >= 85) return 'red'
  if (s >= 70) return 'orange'
  return 'default'
}

// P18.2: 改用递归格式化避免嵌套对象显示 [object Object]
const formatDetail = (d: Record<string, unknown> | null) => formatScoreDetail(d)

// =================================================================
// P19.8: 评分明细 Modal + 雷达图
// =================================================================
const detailOpen = ref(false)
const detailRow = ref<LhbRow | null>(null)
const radarEl = ref<HTMLDivElement | null>(null)
let radarChart: echarts.ECharts | null = null

const detailTitle = computed(() => {
  if (!detailRow.value) return '评分明细'
  const r = detailRow.value
  const name = r.name ? ` ${r.name}` : ''
  return `评分明细 · ${r.ts_code}${name} · ${fmtDate(r.trade_date)}`
})

function openDetailModal(row: LhbRow) {
  detailRow.value = row
  detailOpen.value = true
  nextTick(() => renderRadar(row))
}

function onDetailClose() {
  if (radarChart) {
    radarChart.dispose()
    radarChart = null
  }
  detailRow.value = null
}

// 三组(fund/hist/tech)的配色,与 glossary 术语分类对齐
const RADAR_GROUP_COLORS = ['#1677ff', '#faad14', '#52c41a']

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
      axisName: {
        color: '#595959',
        fontSize: 11,
      },
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
    // 用颜色分组高亮坐标轴标签(可选装饰)
    // 这里用最小实现,后续可进一步丰富
  })
  // 窗口 resize 时自适应
  const onResize = () => radarChart?.resize()
  window.addEventListener('resize', onResize, { once: true })
  // 保存 color 数组避免 tree-shaking 报未使用
  void RADAR_GROUP_COLORS
}

async function load() {
  loading.value = true
  try {
    const data = await fetchLhb({
      // P19.6b: DateRange { start, end } 转 YYYYMMDD
      date_from: form.range.start ? toYmd(form.range.start) : undefined,
      date_to: form.range.end ? toYmd(form.range.end) : undefined,
      score_min: form.scoreMin,
      reason: form.reason || undefined,
      order_by: 'score',
      order_desc: true,
      limit: 500,
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
  form.range = defaultRange()  // P19.6b: 重置为默认一年
  form.scoreMin = 70
  form.reason = undefined
  load()
}

const summary = computed(() => summarize(rows.value, 5))
const tnSummary = computed(() => tnSummarize(rows.value))
const dimScore = computed(() => scoreBuckets(rows.value))
const dimNet = computed(() => netBuckets(rows.value))
const dimDaily = computed(() => dailyBuckets(rows.value))
const dailyMax = computed(() => dimDaily.value.reduce((m, b) => Math.max(m, b.count), 0) || 1)

function pct(n: number, total: number): number {
  if (!total) return 0
  return Math.min(100, Math.round((n / total) * 100))
}

onMounted(load)
</script>

<style scoped>
.signals-page { padding: 12px; }
.dim-card :deep(.ant-card-body) { padding: 12px 16px; }
.dim-bar {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 6px; font-size: 12px;
}
.dim-label { min-width: 68px; color: #595959; }
.dim-label.mono { font-family: 'SF Mono', Menlo, monospace; font-size: 11px; }
.dim-track {
  flex: 1; height: 10px; background: #f5f5f5;
  border-radius: 5px; overflow: hidden;
}
.dim-fill {
  height: 100%; border-radius: 5px;
  transition: width 0.3s ease;
}
.dim-fill.score { background: linear-gradient(90deg, #faad14, #d4380d); }
.dim-fill.net { background: linear-gradient(90deg, #1677ff, #722ed1); }
.dim-fill.daily { background: linear-gradient(90deg, #52c41a, #13c2c2); }
.dim-count {
  min-width: 32px; text-align: right;
  font-family: 'SF Mono', Menlo, monospace;
  color: #262626; font-weight: 500;
}
.daily-rows { max-height: 220px; overflow-y: auto; }
</style>

<style>
/* 非 scoped: popover 内容在 teleport 节点里, scoped class 不能穿透 */
.score-detail-pre {
  margin: 0;
  font-family: 'SF Mono', Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: #333;
  white-space: pre-wrap;
  word-break: break-all;
  max-width: 340px;
}
</style>
