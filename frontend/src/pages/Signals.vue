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
            <a-descriptions-item label="平均评分">
              {{ summary.avgScore != null ? summary.avgScore.toFixed(2) : '-' }}
            </a-descriptions-item>
            <a-descriptions-item label="净买入合计">{{ fmtAmount(summary.netSum) }}</a-descriptions-item>
          </a-descriptions>
          <a-divider style="margin: 12px 0;" />
          <div style="font-weight: 500; margin-bottom: 8px;">前 3 上榜原因</div>
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Empty } from 'ant-design-vue'
import { fetchLhb, type LhbRow } from '@/api/lhb'
import { fmtAmount, fmtDate } from '@/utils/format'
import StockLink from '@/components/StockLink.vue'

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

const summary = computed(() => {
  const list = rows.value
  const count = list.length
  const scores = list.map((r) => r.score).filter((v): v is number => v != null)
  const avgScore = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null
  const netSum = list.reduce((a, r) => a + (r.net_amount ?? 0), 0)
  const reasonCnt = new Map<string, number>()
  for (const r of list) {
    const k = (r.reason ?? '').trim() || '(未知)'
    reasonCnt.set(k, (reasonCnt.get(k) ?? 0) + 1)
  }
  const topReasons = [...reasonCnt.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([reason, cnt]) => ({ reason, count: cnt }))
  return { count, avgScore, netSum, topReasons }
})

onMounted(load)
</script>

<style scoped>
.signals-page { padding: 12px; }
</style>
