<template>
  <div class="lhb-page">
    <a-card :bordered="false" :body-style="{ paddingBottom: 0 }">
      <a-form layout="inline" :model="form" @submit.prevent="onSearch">
        <a-form-item label="日期">
          <a-range-picker
            v-model:value="form.range"
            value-format="YYYY-MM-DD"
            allow-clear
          />
        </a-form-item>
        <a-form-item label="股票">
          <a-input v-model:value="form.ts_code" placeholder="000001.SZ" allow-clear style="width: 140px;" />
        </a-form-item>
        <a-form-item label="原因">
          <a-input v-model:value="form.reason" placeholder="关键字" allow-clear style="width: 160px;" />
        </a-form-item>
        <a-form-item label="净额≥">
          <a-input-number v-model:value="form.net_amount_min" :step="1000000" style="width: 140px;" />
        </a-form-item>
        <a-form-item label="评分≥">
          <a-input-number v-model:value="form.score_min" :min="0" :max="100" style="width: 100px;" />
        </a-form-item>
        <a-form-item>
          <a-space>
            <a-button type="primary" html-type="submit" :loading="store.loading">查询</a-button>
            <a-button @click="onReset">重置</a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-card>

    <a-card :bordered="false" style="margin-top: 12px;">
      <AgGridVue
        class="ag-theme-quartz"
        style="width: 100%; height: 560px;"
        :columnDefs="columnDefs"
        :rowData="store.list.rows"
        :defaultColDef="defaultColDef"
        :animateRows="true"
        :suppressCellFocus="true"
        @row-clicked="onRowClick"
        @sort-changed="onSortChanged"
      />
      <div class="footer-bar">
        <span>共 {{ store.list.total }} 条</span>
        <a-pagination
          :current="page"
          :page-size="pageSize"
          :total="store.list.total"
          :page-size-options="['20', '50', '100', '200']"
          :show-size-changer="true"
          @change="onPageChange"
          @show-size-change="onSizeChange"
        />
      </div>
    </a-card>

    <a-drawer
      v-model:open="drawerOpen"
      :title="drawerTitle"
      width="640"
      :destroy-on-close="true"
    >
      <a-spin :spinning="store.seatsLoading">
        <a-empty v-if="!store.seats.length && !store.seatsLoading" description="无席位数据" />
        <a-table
          v-else
          :data-source="store.seats"
          :columns="seatColumns"
          :pagination="false"
          :row-key="seatRowKey"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'direction'">
              <a-tag :color="record.direction === 'BUY' ? 'red' : 'green'">
                {{ record.direction === 'BUY' ? '买入' : '卖出' }}
              </a-tag>
            </template>
            <template v-else-if="['buy_amount','sell_amount','net_amount'].includes(column.key)">
              {{ fmtAmount(record[column.key]) }}
            </template>
          </template>
        </a-table>
      </a-spin>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { AgGridVue } from 'ag-grid-vue3'
import {
  ModuleRegistry, AllCommunityModule,
  type ColDef, type RowClickedEvent, type SortChangedEvent,
  type GridApi,
} from 'ag-grid-community'
import 'ag-grid-community/styles/ag-theme-quartz.css'
import { useLhbStore } from '@/stores/lhb'
import type { LhbRow, LhbListParams, LhbSeatRow } from '@/api/lhb'
import { fmtAmount, fmtPct, fmtDate } from '@/utils/format'
import { useStockDrill } from '@/composables/useStockDrill'

ModuleRegistry.registerModules([AllCommunityModule])

const store = useLhbStore()
const drill = useStockDrill()

interface FormState {
  range: [string, string] | null
  ts_code?: string
  reason?: string
  net_amount_min?: number
  score_min?: number
}

const form = reactive<FormState>({
  range: null,
  ts_code: undefined,
  reason: undefined,
  net_amount_min: undefined,
  score_min: undefined,
})

const gridApi = ref<GridApi | null>(null)

const columnDefs = computed<ColDef<LhbRow>[]>(() => [
  {
    headerName: '股票', field: 'ts_code', width: 200, pinned: 'left',
    filter: 'agTextColumnFilter',
    cellRenderer: (p: { value: string; data: LhbRow }) => {
      const code = p.value ?? ''
      const name = p.data?.name ?? ''
      const nameHtml = name
        ? `<span style="color:#595959;font-weight:500;margin-left:6px;">${name}</span>`
        : ''
      return `<a class="stock-link" data-ts="${code}" style="color:#1677ff;font-family:'SF Mono',Menlo,monospace;">${code}</a>${nameHtml}`
    },
    onCellClicked: (ev: { value: string }) => {
      if (ev.value) drill.open(String(ev.value))
    },
  },
  {
    headerName: '日期', field: 'trade_date', width: 120,
    valueFormatter: (p) => fmtDate(p.value as string | null),
    sortable: true,
  },
  { headerName: '上榜原因', field: 'reason', flex: 1, minWidth: 200, tooltipField: 'reason' },
  {
    headerName: '收盘', field: 'close', width: 100, type: 'numericColumn',
    valueFormatter: (p) => (p.value != null ? Number(p.value).toFixed(2) : '-'),
  },
  {
    headerName: '涨幅', field: 'pct_change', width: 100, type: 'numericColumn',
    valueFormatter: (p) => fmtPct(p.value as number | null),
    cellStyle: (p) => ({ color: (p.value ?? 0) >= 0 ? '#d4380d' : '#389e0d' }),
    sortable: true,
  },
  {
    headerName: '买入', field: 'buy_amount', width: 120, type: 'numericColumn',
    valueFormatter: (p) => fmtAmount(p.value as number | null),
  },
  {
    headerName: '卖出', field: 'sell_amount', width: 120, type: 'numericColumn',
    valueFormatter: (p) => fmtAmount(p.value as number | null),
  },
  {
    headerName: '净额', field: 'net_amount', width: 120, type: 'numericColumn',
    valueFormatter: (p) => fmtAmount(p.value as number | null),
    cellStyle: (p) => ({ color: (p.value ?? 0) >= 0 ? '#d4380d' : '#389e0d' }),
    sortable: true,
  },
  {
    headerName: '评分', field: 'score', width: 90, type: 'numericColumn',
    valueFormatter: (p) => (p.value != null ? Number(p.value).toFixed(1) : '-'),
    sortable: true,
  },
])

const defaultColDef: ColDef = {
  resizable: true, sortable: true,
  filter: true, floatingFilter: true,
}

const page = computed(() => Math.floor((store.list.offset ?? 0) / (store.list.limit ?? 50)) + 1)
const pageSize = computed(() => store.list.limit ?? 50)

function buildParams(): Partial<LhbListParams> {
  return {
    date_from: form.range?.[0] || undefined,
    date_to: form.range?.[1] || undefined,
    ts_code: form.ts_code || undefined,
    reason: form.reason || undefined,
    net_amount_min: form.net_amount_min ?? undefined,
    score_min: form.score_min ?? undefined,
  }
}

function onSearch() {
  store.search(buildParams()).catch(() => { /* toast 已处理 */ })
}

function onReset() {
  form.range = null
  form.ts_code = undefined
  form.reason = undefined
  form.net_amount_min = undefined
  form.score_min = undefined
  store.search({
    date_from: undefined, date_to: undefined, ts_code: undefined,
    reason: undefined, net_amount_min: undefined, score_min: undefined,
    order_by: 'trade_date', order_desc: true,
  }).catch(() => {})
}

function onPageChange(p: number, size: number) {
  store.page((p - 1) * size, size).catch(() => {})
}

function onSizeChange(_current: number, size: number) {
  store.search({ limit: size }).catch(() => {})
}

const SORTABLE = new Set(['trade_date', 'score', 'net_amount', 'pct_change'])

function onSortChanged(ev: SortChangedEvent) {
  const state = ev.api.getColumnState().find((c) => c.sort)
  if (!state || !state.colId || !SORTABLE.has(state.colId)) return
  store.search({
    order_by: state.colId as LhbListParams['order_by'],
    order_desc: state.sort === 'desc',
  }).catch(() => {})
}

// 席位抽屉
const drawerOpen = ref(false)
const drawerRow = ref<LhbRow | null>(null)
const drawerTitle = computed(() => {
  if (!drawerRow.value) return '席位明细'
  const r = drawerRow.value
  const nameTag = r.name ? ` ${r.name}` : ''
  return `席位明细 · ${r.ts_code}${nameTag} · ${fmtDate(r.trade_date)}`
})

// P10.4: 对齐后端实际字段 rank_no / broker_name, 金额走 fmtAmount 自动万/亿
const seatColumns = [
  {
    title: '方向', dataIndex: 'direction', key: 'direction', width: 70,
    sorter: (a: LhbSeatRow, b: LhbSeatRow) => a.direction.localeCompare(b.direction),
  },
  {
    title: '排位', dataIndex: 'rank_no', key: 'rank_no', width: 70, align: 'right' as const,
    sorter: (a: LhbSeatRow, b: LhbSeatRow) => (a.rank_no ?? 99) - (b.rank_no ?? 99),
    defaultSortOrder: 'ascend' as const,
  },
  {
    title: '席位名称', dataIndex: 'broker_name', key: 'broker_name', ellipsis: true,
    sorter: (a: LhbSeatRow, b: LhbSeatRow) =>
      (a.broker_name ?? '').localeCompare(b.broker_name ?? ''),
  },
  {
    title: '买入', dataIndex: 'buy_amount', key: 'buy_amount', width: 110, align: 'right' as const,
    sorter: (a: LhbSeatRow, b: LhbSeatRow) => (a.buy_amount ?? 0) - (b.buy_amount ?? 0),
  },
  {
    title: '卖出', dataIndex: 'sell_amount', key: 'sell_amount', width: 110, align: 'right' as const,
    sorter: (a: LhbSeatRow, b: LhbSeatRow) => (a.sell_amount ?? 0) - (b.sell_amount ?? 0),
  },
  {
    title: '净额', dataIndex: 'net_amount', key: 'net_amount', width: 110, align: 'right' as const,
    sorter: (a: LhbSeatRow, b: LhbSeatRow) => (a.net_amount ?? 0) - (b.net_amount ?? 0),
    defaultSortOrder: 'descend' as const,
  },
]

function seatRowKey(r: LhbSeatRow) {
  return `${r.direction}-${r.rank_no ?? 0}-${r.broker_name}`
}

function onRowClick(ev: RowClickedEvent<LhbRow>) {
  const row = ev.data
  if (!row || !row.trade_date) return
  drawerRow.value = row
  drawerOpen.value = true
  store.loadSeats({
    trade_date: row.trade_date.slice(0, 10),
    ts_code: row.ts_code,
    limit: 20,
  }).catch(() => {})
}

onMounted(() => { onSearch() })
</script>

<style scoped>
.lhb-page .footer-bar {
  display: flex; justify-content: space-between; align-items: center;
  margin-top: 12px; color: #8c8c8c;
}
</style>
