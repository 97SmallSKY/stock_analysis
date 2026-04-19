<template>
  <div class="signals-page">
    <a-card :bordered="false" :body-style="{ paddingBottom: 8 }">
      <a-form layout="inline" :model="form" @submit.prevent="onSearch">
        <a-form-item label="日期">
          <a-range-picker
            v-model:value="form.range"
            value-format="YYYY-MM-DD"
            allow-clear
          />
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
            <a-button type="primary" html-type="submit" :loading="loading">查询</a-button>
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
                  {{ fmtAmount(record.net_amount) }}
                </span>
              </template>
              <template v-else-if="column.key === 'score'">
                <a-tag :color="scoreColor(record.score)">
                  {{ record.score != null ? Number(record.score).toFixed(1) : '-' }}
                </a-tag>
              </template>
              <template v-else-if="column.key === 'score_detail'">
                <a-tooltip v-if="record.score_detail" :title="formatDetail(record.score_detail)">
                  <a-button type="link" size="small">详情</a-button>
                </a-tooltip>
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
            <a-descriptions-item label="净买入合计">{{ fmtAmount(summary.netSum) }}</a-descriptions-item>
            <a-descriptions-item label="净买入均值">{{ fmtAmount(summary.netAvg) }}</a-descriptions-item>
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Empty } from 'ant-design-vue'
import { fetchLhb, type LhbRow } from '@/api/lhb'
import { fmtAmount, fmtDate } from '@/utils/format'
import StockLink from '@/components/StockLink.vue'
import {
  summarize, scoreBuckets, netBuckets, dailyBuckets,
} from '@/utils/signals_agg'

const simpleImage = Empty.PRESENTED_IMAGE_SIMPLE

interface FormState {
  range: [string, string] | null
  scoreMin: number
  reason?: string
}
const form = reactive<FormState>({ range: null, scoreMin: 70, reason: undefined })

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
  { title: '评分明细', key: 'score_detail', width: 90 },
]

function rowKey(r: LhbRow) { return `${r.ts_code}_${r.trade_date}` }

function scoreColor(s: number | null) {
  if (s == null) return 'default'
  if (s >= 85) return 'red'
  if (s >= 70) return 'orange'
  return 'default'
}

function formatDetail(d: Record<string, unknown> | null) {
  if (!d) return ''
  return Object.entries(d).map(([k, v]) => `${k}: ${v}`).join('\n')
}

async function load() {
  loading.value = true
  try {
    const data = await fetchLhb({
      date_from: form.range?.[0]?.replace(/-/g, '') || undefined,
      date_to: form.range?.[1]?.replace(/-/g, '') || undefined,
      score_min: form.scoreMin,
      reason: form.reason || undefined,
      order_by: 'score',
      order_desc: true,
      limit: 500,
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
  form.scoreMin = 70
  form.reason = undefined
  load()
}

const summary = computed(() => summarize(rows.value, 5))
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
