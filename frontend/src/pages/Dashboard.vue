<template>
  <div class="dashboard">
    <!-- KPI 四柱 -->
    <a-row :gutter="16" class="kpi-row">
      <a-col :xs="12" :md="6">
        <a-card :bordered="false" class="kpi-card kpi-score" :loading="loadingFirst">
          <div class="kpi-title">
            <ThunderboltOutlined /> 市场情绪
          </div>
          <div class="kpi-value" :style="{ color: scoreColor(s?.sentiment_score) }">
            {{ s?.sentiment_score != null ? Number(s.sentiment_score).toFixed(2) : '-' }}
          </div>
          <div class="kpi-sub">{{ fmtDate(s?.trade_date) }} · 样本 {{ s?.total_stocks ?? '-' }}</div>
        </a-card>
      </a-col>
      <a-col :xs="12" :md="6">
        <a-card :bordered="false" class="kpi-card kpi-up" :loading="loadingFirst">
          <div class="kpi-title"><RiseOutlined /> 涨 / 跌</div>
          <!-- 2026-04-20: market_sentiment 表的 up_count/down_count 暂未聚合(任务 #11),
               fallback 从 0 改为 '-',避免误导用户「今天全市场没涨跌」 -->
          <div class="kpi-value">
            <span class="up">{{ s?.up_count ?? '-' }}</span>
            <span class="divider">/</span>
            <span class="down">{{ s?.down_count ?? '-' }}</span>
          </div>
          <div class="kpi-sub">均幅 {{ fmtPct(s?.avg_pct_change) }}</div>
        </a-card>
      </a-col>
      <a-col :xs="12" :md="6">
        <a-card :bordered="false" class="kpi-card kpi-lu" :loading="loadingFirst">
          <div class="kpi-title"><FireOutlined /> 涨停 / 跌停</div>
          <div class="kpi-value">
            <span class="up">{{ s?.limit_up_count ?? 0 }}</span>
            <span class="divider">/</span>
            <span class="down">{{ s?.limit_down_count ?? 0 }}</span>
          </div>
          <div class="kpi-sub">封板强度</div>
        </a-card>
      </a-col>
      <a-col :xs="12" :md="6">
        <a-card :bordered="false" class="kpi-card kpi-top1" :loading="loadingFirst">
          <div class="kpi-title"><CrownOutlined /> 今日龙虎榜 Top1</div>
          <div class="kpi-value">
            <StockLink v-if="top1" :code="top1.ts_code" :name="top1.name" />
            <span v-else>-</span>
          </div>
          <div class="kpi-sub">
            净买入
            <span :style="{ color: (top1?.net_amount ?? 0) >= 0 ? '#d4380d' : '#389e0d' }">
              {{ fmtAmountWan(top1?.net_amount) }}
            </span>
          </div>
        </a-card>
      </a-col>
    </a-row>

    <!-- 数据源状态 + 龙虎榜表 -->
    <a-row :gutter="16" style="margin-top: 16px;">
      <a-col :xs="24" :md="16">
        <a-card
          :bordered="false"
          :loading="store.loading && !topLhb.length"
          class="dash-table-card"
        >
          <template #title>
            <a-space>
              <ThunderboltOutlined style="color: #d4380d;" />
              <span>今日龙虎榜 Top</span>
              <a-select
                v-model:value="topN"
                size="small"
                style="width: 100px;"
                :options="[5, 10, 20, 50].map(n => ({ value: n, label: `Top ${n}` }))"
                @change="reload"
              />
            </a-space>
          </template>
          <template #extra>
            <a-button type="text" size="small" @click="reload">
              <template #icon><ReloadOutlined /></template>
              刷新
            </a-button>
          </template>
          <a-table
            :columns="lhbColumns"
            :data-source="topLhb"
            :pagination="false"
            :row-key="rowKey"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'ts_code'">
                <StockLink :code="record.ts_code" :name="record.name" />
              </template>
              <template v-else-if="column.key === 'trade_date'">
                {{ fmtDate(record.trade_date) }}
              </template>
              <template v-else-if="column.key === 'net_amount'">
                <span :style="{ color: (record.net_amount ?? 0) >= 0 ? '#d4380d' : '#389e0d', fontWeight: 500 }">
                  {{ fmtAmountWan(record.net_amount) }}
                </span>
              </template>
              <template v-else-if="column.key === 'score'">
                <a-tag :color="scoreTagColor(record.score)" class="score-tag">
                  {{ record.score != null ? record.score.toFixed(1) : '-' }}
                </a-tag>
              </template>
            </template>
          </a-table>
        </a-card>
      </a-col>

      <a-col :xs="24" :md="8">
        <a-card
          :bordered="false"
          :loading="store.loading && !adapters.length"
          class="dash-adapters-card"
        >
          <template #title>
            <a-space>
              <DatabaseOutlined style="color: #1677ff;" />
              <span>数据源状态</span>
            </a-space>
          </template>
          <a-empty v-if="!adapters.length" description="无适配器" />
          <a-list v-else size="small" :data-source="adapters" :split="false">
            <template #renderItem="{ item }">
              <a-list-item class="adapter-item">
                <a-space>
                  <span class="dot" :style="{ background: healthColor(item.last_health_ok) }" />
                  <span class="adapter-name">{{ item.name }}</span>
                </a-space>
                <template #actions>
                  <a-tag :color="cbColor(item.circuit_state)">
                    {{ item.circuit_state || '-' }}
                  </a-tag>
                  <span class="kpi-sub">
                    {{ item.last_latency_ms != null ? item.last_latency_ms + ' ms' : '-' }}
                  </span>
                </template>
              </a-list-item>
            </template>
          </a-list>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  ThunderboltOutlined, RiseOutlined, FireOutlined, CrownOutlined,
  DatabaseOutlined, ReloadOutlined,
} from '@ant-design/icons-vue'
import { useDashboardStore } from '@/stores/dashboard'
import type { TopLhbRow } from '@/api/dashboard'
import {
  fmtAmountWan, fmtPct, fmtDate, healthColor, cbColor,
} from '@/utils/format'
import StockLink from '@/components/StockLink.vue'

const store = useDashboardStore()
const topN = ref(10)

const s = computed(() => store.data?.sentiment ?? null)
const topLhb = computed<TopLhbRow[]>(() => store.data?.top_lhb ?? [])
const top1 = computed<TopLhbRow | null>(() => topLhb.value[0] ?? null)
const adapters = computed(() => store.data?.adapters ?? [])
const loadingFirst = computed(() => store.loading && !store.data)

const lhbColumns = [
  {
    title: '股票', dataIndex: 'ts_code', key: 'ts_code', width: 180,
    sorter: (a: TopLhbRow, b: TopLhbRow) => a.ts_code.localeCompare(b.ts_code),
  },
  {
    title: '日期', dataIndex: 'trade_date', key: 'trade_date', width: 110,
    sorter: (a: TopLhbRow, b: TopLhbRow) =>
      (a.trade_date ?? '').localeCompare(b.trade_date ?? ''),
  },
  { title: '上榜原因', dataIndex: 'reason', key: 'reason', ellipsis: true },
  {
    title: '净额', dataIndex: 'net_amount', key: 'net_amount', width: 130, align: 'right' as const,
    sorter: (a: TopLhbRow, b: TopLhbRow) =>
      (a.net_amount ?? 0) - (b.net_amount ?? 0),
    defaultSortOrder: 'descend' as const,
  },
  {
    title: '评分', dataIndex: 'score', key: 'score', width: 80, align: 'right' as const,
    sorter: (a: TopLhbRow, b: TopLhbRow) => (a.score ?? -1) - (b.score ?? -1),
  },
]

function rowKey(r: TopLhbRow): string {
  return `${r.ts_code}-${r.trade_date ?? ''}`
}

function scoreColor(v: number | null | undefined): string {
  if (v == null) return '#8c8c8c'
  if (v >= 0.5) return '#d4380d'
  if (v <= -0.5) return '#389e0d'
  return '#faad14'
}

function scoreTagColor(v: number | null | undefined): string {
  if (v == null) return 'default'
  if (v >= 80) return 'red'
  if (v >= 60) return 'orange'
  if (v >= 40) return 'blue'
  return 'default'
}

function reload() {
  store.load(topN.value, true).catch(() => { /* 错误已由 http 拦截器 toast */ })
}

onMounted(reload)
</script>

<style scoped>
.dashboard .kpi-row { margin-bottom: 0; }
.kpi-card {
  position: relative;
  overflow: hidden;
  border-radius: 10px;
}
.kpi-card::before {
  content: '';
  position: absolute; left: 0; top: 0; bottom: 0; width: 4px;
  background: #d4380d;
  opacity: .85;
}
.kpi-up::before    { background: #fa8c16; }
.kpi-lu::before    { background: #cf1322; }
.kpi-top1::before  { background: #722ed1; }
.kpi-title {
  color: #8c8c8c; font-size: 12px;
  display: flex; align-items: center; gap: 6px;
}
.kpi-value {
  margin-top: 8px;
  font-size: 28px; font-weight: 600;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.kpi-value .up   { color: #d4380d; }
.kpi-value .down { color: #389e0d; }
.kpi-value .divider {
  color: #bfbfbf; margin: 0 8px; font-weight: 300;
}
.kpi-sub {
  color: #8c8c8c; font-size: 12px; margin-top: 6px;
}

.dash-table-card :deep(.ant-card-body) { padding-top: 12px; }
.dash-adapters-card :deep(.ant-list-item) {
  padding: 8px 0;
  border-bottom: 1px dashed #f0f0f0 !important;
}
.adapter-item .dot {
  display: inline-block; width: 10px; height: 10px;
  border-radius: 50%;
  box-shadow: 0 0 0 2px rgba(0,0,0,.04);
}
.adapter-name { font-family: 'SF Mono', Menlo, monospace; font-size: 12px; }

.score-tag { min-width: 44px; text-align: center; }
</style>
