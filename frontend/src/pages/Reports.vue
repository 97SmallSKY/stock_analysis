<template>
  <div class="reports-page">
    <a-card :bordered="false" title="周报">
      <template #extra>
        <a-space>
          <!-- P19.6e (2026-04-20): 周报默认近 1 周(与功能语义匹配,不用一年)
               P19.10: 点击即查 -->
          <DateRangeQuickPick v-model="range" :options="['1m', '1w', '5d', 'today']" @change="load" />
          <a-button :loading="loading" @click="load">查询</a-button>
          <a-button type="primary" :loading="loading" @click="generate">
            生成本周报
          </a-button>
          <a-button :disabled="!data" @click="exportCsv">导出 CSV</a-button>
        </a-space>
      </template>

      <a-empty v-if="!data && !loading" description="暂无数据" />
      <a-spin v-else :spinning="loading">
        <a-row v-if="data" :gutter="16">
          <a-col :span="6">
            <a-statistic title="龙虎榜条数" :value="data.lhb_total" />
            <div class="sub">{{ data.start_date }} ~ {{ data.end_date }}</div>
          </a-col>
          <a-col :span="9">
            <h4>上榜原因 TOP 5</h4>
            <a-list size="small" :data-source="data.lhb_top_reasons">
              <template #renderItem="{ item }">
                <a-list-item>
                  <span>{{ item.reason }}</span>
                  <a-tag>{{ item.cnt }}</a-tag>
                </a-list-item>
              </template>
            </a-list>
          </a-col>
          <a-col :span="9">
            <h4>机器人盈亏</h4>
            <a-list size="small" :data-source="data.robot_summary">
              <template #renderItem="{ item }">
                <a-list-item>
                  <span>#{{ item.robot_id }}</span>
                  <span :style="{ color: (item.net_pnl ?? 0) >= 0 ? '#d4380d' : '#52c41a' }">
                    {{ fmtAmount(item.net_pnl) }}
                  </span>
                  <span class="sub">{{ item.entries }} 笔</span>
                </a-list-item>
              </template>
            </a-list>
          </a-col>
        </a-row>

        <a-divider v-if="data" />

        <a-row v-if="data" :gutter="16">
          <a-col :span="12">
            <h4>净买入 TOP 10</h4>
            <a-table
              :columns="lhbCols"
              :data-source="data.lhb_top_net"
              :row-key="(r: any) => `${r.ts_code}-${r.trade_date}`"
              :pagination="false"
              size="small"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'net_amount'">
                  {{ fmtAmountWan(record.net_amount) }}
                </template>
              </template>
            </a-table>
          </a-col>
          <a-col :span="12">
            <h4>回测 TOP 10 (按年化)</h4>
            <a-table
              :columns="btCols"
              :data-source="data.backtest_top"
              :row-key="(r: any) => r.task_id"
              :pagination="false"
              size="small"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'annual_return'">
                  {{ fmtPct((record.annual_return ?? 0) * 100) }}
                </template>
                <template v-else-if="column.key === 'max_drawdown'">
                  {{ fmtPct((record.max_drawdown ?? 0) * 100) }}
                </template>
              </template>
            </a-table>
          </a-col>
        </a-row>

        <a-alert
          v-if="data && data.errors?.length"
          type="warning" show-icon style="margin-top: 12px;"
          :message="`部分模块失败: ${data.errors.join('; ')}`"
        />
      </a-spin>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { fetchWeeklyReport, generateWeeklyReport, type WeeklyReport } from '@/api/reports'
import { fmtAmount, fmtAmountWan, fmtPct } from '@/utils/format'
import { downloadCsv, rowsToCsv } from '@/utils/csv'
import DateRangeQuickPick from '@/components/DateRangeQuickPick.vue'
import { calcRange, type DateRange } from '@/utils/date_range'

// P19.6e: 周报默认 1 周,与功能语义匹配
const range = ref<DateRange>(calcRange('1w'))
const data = ref<WeeklyReport | null>(null)
const loading = ref(false)

const lhbCols = [
  { title: '股票', dataIndex: 'ts_code', key: 'ts_code', width: 110 },
  { title: '日期', dataIndex: 'trade_date', key: 'trade_date', width: 110 },
  { title: '原因', dataIndex: 'reason', key: 'reason', ellipsis: true },
  { title: '净额', dataIndex: 'net_amount', key: 'net_amount', width: 110, align: 'right' },
  { title: '评分', dataIndex: 'score', key: 'score', width: 70, align: 'right' },
]

const btCols = [
  { title: '策略', dataIndex: 'strategy_name', key: 'strategy_name' },
  { title: '年化', key: 'annual_return', width: 80, align: 'right' },
  { title: '回撤', key: 'max_drawdown', width: 80, align: 'right' },
  { title: '完成', dataIndex: 'finished_at', key: 'finished_at', width: 180 },
]

async function load() {
  loading.value = true
  try {
    // P19.6e: DateRange { start, end } 直接算 days
    const end: string | undefined = range.value.end || undefined
    let days = 7
    if (range.value.start && range.value.end) {
      const a = new Date(range.value.start)
      const b = new Date(range.value.end)
      days = Math.max(1, Math.round((b.getTime() - a.getTime()) / 86400000) + 1)
    }
    data.value = await fetchWeeklyReport(end, days)
  } finally {
    loading.value = false
  }
}

async function generate() {
  loading.value = true
  try {
    data.value = await generateWeeklyReport(7)
    message.success('已生成')
  } finally {
    loading.value = false
  }
}

function exportCsv() {
  if (!data.value) return
  const csv = rowsToCsv(data.value.lhb_top_net as unknown as Record<string, unknown>[])
  downloadCsv(`weekly_${data.value.start_date}_${data.value.end_date}.csv`, csv)
}

onMounted(load)
</script>

<style scoped>
.reports-page .sub { color: #8c8c8c; font-size: 12px; }
</style>
