<template>
  <div class="dashboard">
    <a-row :gutter="16" style="margin-bottom: 16px;">
      <a-col :span="16">
        <a-card title="今日市场情绪" :bordered="false" :loading="store.loading && !s">
          <a-empty v-if="!s" description="暂无数据" />
          <a-row v-else :gutter="16">
            <a-col :span="8">
              <a-statistic
                title="情绪评分"
                :value="s.sentiment_score ?? 0"
                :precision="2"
                :value-style="{ color: scoreColor(s.sentiment_score) }"
              />
              <div class="sub">
                {{ fmtDate(s.trade_date) }} · 样本 {{ s.total_stocks ?? '-' }}
              </div>
            </a-col>
            <a-col :span="8">
              <a-statistic
                title="涨 / 跌"
                :value="`${s.up_count ?? 0} / ${s.down_count ?? 0}`"
                :value-style="{ fontSize: '20px' }"
              />
              <div class="sub">均幅 {{ fmtPct(s.avg_pct_change) }}</div>
            </a-col>
            <a-col :span="8">
              <a-statistic
                title="涨停 / 跌停"
                :value="`${s.limit_up_count ?? 0} / ${s.limit_down_count ?? 0}`"
                :value-style="{ fontSize: '20px', color: '#d4380d' }"
              />
            </a-col>
          </a-row>
        </a-card>
      </a-col>

      <a-col :span="8">
        <a-card title="数据源状态" :bordered="false" :loading="store.loading && !adapters.length">
          <a-empty v-if="!adapters.length" description="无适配器" />
          <a-list v-else size="small" :data-source="adapters">
            <template #renderItem="{ item }">
              <a-list-item>
                <a-space>
                  <span
                    class="dot"
                    :style="{ background: healthColor(item.last_health_ok) }"
                  />
                  <span class="adapter-name">{{ item.name }}</span>
                </a-space>
                <template #actions>
                  <a-tag :color="cbColor(item.circuit_state)">
                    {{ item.circuit_state || '-' }}
                  </a-tag>
                  <span class="sub">
                    {{ item.last_latency_ms != null ? item.last_latency_ms + ' ms' : '-' }}
                  </span>
                </template>
              </a-list-item>
            </template>
          </a-list>
        </a-card>
      </a-col>
    </a-row>

    <a-card
      :bordered="false"
      :loading="store.loading && !topLhb.length"
    >
      <template #title>
        <a-space>
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
        <a-button size="small" @click="reload">刷新</a-button>
      </template>
      <a-table
        :columns="lhbColumns"
        :data-source="topLhb"
        :pagination="false"
        :row-key="rowKey"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'trade_date'">
            {{ fmtDate(record.trade_date) }}
          </template>
          <template v-else-if="column.key === 'net_amount'">
            <span :style="{ color: (record.net_amount ?? 0) >= 0 ? '#d4380d' : '#52c41a' }">
              {{ fmtAmount(record.net_amount) }}
            </span>
          </template>
          <template v-else-if="column.key === 'score'">
            <a-tag :color="scoreTagColor(record.score)">
              {{ record.score != null ? record.score.toFixed(1) : '-' }}
            </a-tag>
          </template>
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useDashboardStore } from '@/stores/dashboard'
import type { TopLhbRow } from '@/api/dashboard'
import {
  fmtAmount, fmtPct, fmtDate, healthColor, cbColor,
} from '@/utils/format'

const store = useDashboardStore()
const topN = ref(10)

const s = computed(() => store.data?.sentiment ?? null)
const topLhb = computed<TopLhbRow[]>(() => store.data?.top_lhb ?? [])
const adapters = computed(() => store.data?.adapters ?? [])

const lhbColumns = [
  { title: '股票', dataIndex: 'ts_code', key: 'ts_code', width: 110 },
  { title: '日期', dataIndex: 'trade_date', key: 'trade_date', width: 110 },
  { title: '上榜原因', dataIndex: 'reason', key: 'reason', ellipsis: true },
  { title: '净额', dataIndex: 'net_amount', key: 'net_amount', width: 120, align: 'right' },
  { title: '评分', dataIndex: 'score', key: 'score', width: 80, align: 'right' },
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
.dashboard .sub {
  color: #8c8c8c; font-size: 12px; margin-top: 4px;
}
.dashboard .dot {
  display: inline-block; width: 10px; height: 10px;
  border-radius: 50%;
}
.dashboard .adapter-name { font-family: monospace; }
</style>
