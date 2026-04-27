<template>
  <div class="signals-page">
    <a-card :bordered="false" :body-style="{ paddingBottom: 8 }">
      <a-form layout="inline" :model="form" @submit.prevent="onSearch">
        <a-form-item label="日期">
          <!-- P19.6b (2026-04-20): 默认 1 年。
               P19.10: 点击即查 -->
          <DateRangeQuickPick v-model="form.range" @change="onSearch" />
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
            <template #headerCell="{ column }">
              <TermTooltip v-if="column.termKey" :term="column.termKey">{{ column.title }}</TermTooltip>
            </template>
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'ts_code'">
                <!-- P19.12: 传 anchorDate 让 K 线 Modal 默认以该行日期为中心 -->
                <StockLink :code="record.ts_code" :name="record.name" :anchor-date="record.trade_date" />
              </template>
              <template v-else-if="column.key === 'trade_date'">{{ fmtDate(record.trade_date) }}</template>
              <template v-else-if="column.key === 'reason'">
                <!-- P19.12: 上榜原因旁加跳转,一键回到龙虎榜看完整席位 -->
                <span>
                  {{ record.reason }}
                  <a-tooltip title="在龙虎榜页查看该股票当天的完整席位">
                    <a
                      class="reason-jump"
                      @click.stop="gotoLhb(record)"
                    >
                      <SwapOutlined /> 龙虎榜
                    </a>
                  </a-tooltip>
                </span>
              </template>
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
              <template v-else-if="column.key === 't1_pnl_pct' || column.key === 't3_pnl_pct' || column.key === 't5_pnl_pct' || column.key === 't7_pnl_pct' || column.key === 't10_pnl_pct'">
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

    <!-- P19.8 (2026-04-20) · P19.10 布局优化: 上下结构
         顶部:一行 header 紧凑显示股票/日期/评分/净买入/上榜原因
         中部:雷达图居中 100% 宽
         底部:2 列明细表,避免右侧挤、左下空 -->
    <a-modal
      v-model:open="detailOpen"
      :title="detailTitle"
      width="860px"
      :footer="null"
      destroy-on-close
      @close="onDetailClose"
    >
      <div v-if="detailRow" class="detail-modal">
        <!-- Header: 一行紧凑展示关键信息 -->
        <a-descriptions :column="3" size="small" bordered class="detail-header">
          <a-descriptions-item label="股票">
            <StockLink :code="detailRow.ts_code" :name="detailRow.name" :anchor-date="detailRow.trade_date" />
          </a-descriptions-item>
          <a-descriptions-item label="日期">{{ fmtDate(detailRow.trade_date) }}</a-descriptions-item>
          <a-descriptions-item label="综合评分">
            <a-tag :color="scoreColor(detailRow.score)">{{ detailRow.score ?? '-' }}</a-tag>
          </a-descriptions-item>
          <a-descriptions-item label="净买入" :span="1">{{ fmtAmountWan(detailRow.net_amount) }}</a-descriptions-item>
          <a-descriptions-item label="上榜原因" :span="2">{{ detailRow.reason }}</a-descriptions-item>
        </a-descriptions>

        <!-- P19.12 优化布局:雷达图左 + 3 列分类明细右,横排不下滑 -->
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, nextTick } from 'vue'
import { Empty } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import { SwapOutlined } from '@ant-design/icons-vue'
import { fetchLhb, type LhbRow } from '@/api/lhb'
import { fmtAmountWan, fmtDate } from '@/utils/format'
import StockLink from '@/components/StockLink.vue'
import DateRangeQuickPick from '@/components/DateRangeQuickPick.vue'
import TermTooltip from '@/components/TermTooltip.vue'
import { defaultRange, toYmd, type DateRange } from '@/utils/date_range'

const router = useRouter()

/**
 * P19.12: 点「龙虎榜」链接,带股票代码 + 日期跳转到龙虎榜页,
 * 龙虎榜页会读 URL query 自动过滤。
 */
function gotoLhb(row: LhbRow) {
  router.push({
    path: '/lhb',
    query: {
      ts_code: row.ts_code,
      date: row.trade_date ?? undefined,
    },
  })
}
import {
  summarize, scoreBuckets, netBuckets, dailyBuckets,
} from '@/utils/signals_agg'
import { tnSummarize } from '@/utils/tn_summary'
import { formatScoreDetail, extractRadarData, labelOf } from '@/utils/score_detail'

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
    title: '上榜原因', dataIndex: 'reason', key: 'reason', ellipsis: true, termKey: 'reason',
    sorter: (a: LhbRow, b: LhbRow) => (a.reason ?? '').localeCompare(b.reason ?? ''),
  },
  {
    title: '净买入', key: 'net_amount', dataIndex: 'net_amount', width: 130, align: 'right' as const, termKey: 'net_amount',
    sorter: (a: LhbRow, b: LhbRow) => (a.net_amount ?? 0) - (b.net_amount ?? 0),
  },
  {
    title: '评分', key: 'score', dataIndex: 'score', width: 90, align: 'right' as const, termKey: 'score',
    sorter: (a: LhbRow, b: LhbRow) => (a.score ?? -1) - (b.score ?? -1),
    defaultSortOrder: 'descend' as const,
  },
  // P16.5 + STO-103: T+1 / T+3 / T+5 / T+7 / T+10 次日收益 (lhb_tn_price JOIN)
  {
    title: 'T+1', key: 't1_pnl_pct', dataIndex: 't1_pnl_pct', width: 80, align: 'right' as const, termKey: 't1_pnl_pct',
    sorter: (a: LhbRow, b: LhbRow) => (a.t1_pnl_pct ?? -999) - (b.t1_pnl_pct ?? -999),
  },
  {
    title: 'T+3', key: 't3_pnl_pct', dataIndex: 't3_pnl_pct', width: 80, align: 'right' as const, termKey: 't3_pnl_pct',
    sorter: (a: LhbRow, b: LhbRow) => (a.t3_pnl_pct ?? -999) - (b.t3_pnl_pct ?? -999),
  },
  {
    title: 'T+5', key: 't5_pnl_pct', dataIndex: 't5_pnl_pct', width: 80, align: 'right' as const, termKey: 't5_pnl_pct',
    sorter: (a: LhbRow, b: LhbRow) => (a.t5_pnl_pct ?? -999) - (b.t5_pnl_pct ?? -999),
  },
  {
    title: 'T+7', key: 't7_pnl_pct', dataIndex: 't7_pnl_pct', width: 80, align: 'right' as const, termKey: 't7_pnl_pct',
    sorter: (a: LhbRow, b: LhbRow) => (a.t7_pnl_pct ?? -999) - (b.t7_pnl_pct ?? -999),
  },
  {
    title: 'T+10', key: 't10_pnl_pct', dataIndex: 't10_pnl_pct', width: 90, align: 'right' as const, termKey: 't10_pnl_pct',
    sorter: (a: LhbRow, b: LhbRow) => (a.t10_pnl_pct ?? -999) - (b.t10_pnl_pct ?? -999),
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
// P19.12: 评分明细结构化分组(供 Modal 右侧 3 列布局)
// =================================================================
interface DimItem { k: string; label: string; v: unknown }
interface DimGroup { key: string; title: string; color: string; items: DimItem[] }

const dimGroups = computed<DimGroup[]>(() => {
  const d = detailRow.value?.score_detail as Record<string, unknown> | null | undefined
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
  const d = detailRow.value?.score_detail as Record<string, unknown> | null | undefined
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
  // 约定:后端得分一律 0-100 归一。用分段着色直观展示强弱
  if (v >= 80) return '#d4380d'   // 高分红
  if (v >= 50) return '#faad14'   // 中分橙
  if (v > 0)   return '#52c41a'   // 低分绿
  return '#8c8c8c'                 // 0/异常
}

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
      skip_total: true, // STO-43: 首屏跳过 COUNT(*) 加速,"已加载 N 条" 足够
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

/* P19.12: 「上榜原因」列里跳龙虎榜的小链接 */
.reason-jump {
  margin-left: 8px;
  font-size: 11px;
  color: #1677ff;
  cursor: pointer;
  opacity: 0.7;
  white-space: nowrap;
}
.reason-jump:hover { opacity: 1; text-decoration: underline; }
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
  line-height: 1.6;
  color: #333;
  white-space: pre-wrap;
  word-break: break-word;
  /* P19.10: Modal 里需要多列排版,不再硬限 340px */
}

/* P19.10 · P19.12: 评分明细 Modal 布局 */
.detail-modal .detail-header {
  margin-bottom: 12px;
}

/* P19.12: 下半部分改为左右并排:雷达图 + 分组明细,横排不下滑 */
.detail-modal .detail-body {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) minmax(340px, 1.2fr);
  gap: 16px;
  align-items: start;
}
.detail-modal .detail-radar-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
}
.detail-modal .detail-radar-hint {
  margin-top: 4px;
  font-size: 11px;
  color: #8c8c8c;
  text-align: center;
  line-height: 1.6;
}
.detail-modal .detail-radar {
  width: 100%;
  max-width: 360px;
  height: 300px;
}
.detail-modal .detail-dim-col {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 10px;
}
.detail-modal .dim-group {
  background: #fafbfc;
  border-radius: 6px;
  padding: 8px 10px;
}
.detail-modal .dim-title {
  font-size: 12px;
  font-weight: 600;
  color: #262626;
  padding-left: 8px;
  border-left: 3px solid #ccc;
  margin-bottom: 6px;
}
.detail-modal .dim-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11.5px;
  padding: 2px 0;
  border-bottom: 1px dashed #f0f0f0;
}
.detail-modal .dim-row:last-child { border-bottom: none; }
.detail-modal .dim-name {
  color: #595959;
  white-space: nowrap;
  margin-right: 6px;
}
.detail-modal .dim-value {
  font-family: 'SF Mono', Menlo, monospace;
  font-weight: 500;
  font-size: 12px;
}

/* 窄屏自动堆叠 */
@media (max-width: 720px) {
  .detail-modal .detail-body {
    grid-template-columns: 1fr;
  }
  .detail-modal .detail-dim-col {
    grid-template-columns: 1fr;
  }
}
</style>
