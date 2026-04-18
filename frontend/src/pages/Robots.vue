<template>
  <div class="robots-page">
    <a-card :bordered="false">
      <template #title>
        <span>机器人模拟炒股</span>
      </template>
      <template #extra>
        <a-space>
          <a-button :loading="loading" @click="refresh">刷新</a-button>
          <a-switch
            v-model:checked="autoRefresh"
            checked-children="自动刷新"
            un-checked-children="手动"
          />
        </a-space>
      </template>

      <a-empty v-if="!loading && robots.length === 0" description="暂无机器人注册" />

      <a-table
        v-else
        :data-source="robots"
        :columns="columns"
        :pagination="false"
        :loading="loading"
        :row-key="(r: RobotSnapshot) => r.robot_id"
        :expand-row-by-click="true"
        size="middle"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'state'">
            <a-tag :color="stateColor(record.state)">{{ record.state }}</a-tag>
          </template>
          <template v-else-if="column.key === 'enabled'">
            <a-tag :color="record.enabled ? 'green' : 'default'">
              {{ record.enabled ? '启用' : '禁用' }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'cash' || column.key === 'total_assets'">
            {{ fmtAmount(record[column.key]) }}
          </template>
          <template v-else-if="column.key === 'daily_pnl_pct'">
            <span :style="{ color: (record.daily_pnl_pct ?? 0) >= 0 ? '#d4380d' : '#389e0d' }">
              {{ fmtPct(record.daily_pnl_pct) }}
            </span>
          </template>
          <template v-else-if="column.key === 'actions'">
            <a-space :size="4" @click.stop>
              <a-button
                size="small"
                :loading="acting[record.robot_id] === 'pause'"
                :disabled="record.state !== 'RUNNING'"
                @click="act(record.robot_id, 'pause')"
              >暂停</a-button>
              <a-button
                size="small"
                type="primary"
                :loading="acting[record.robot_id] === 'resume'"
                :disabled="record.state === 'RUNNING'"
                @click="act(record.robot_id, 'resume')"
              >恢复</a-button>
              <a-button
                v-if="!record.enabled"
                size="small"
                :loading="acting[record.robot_id] === 'enable'"
                @click="act(record.robot_id, 'enable')"
              >启用</a-button>
              <a-button
                v-else
                size="small"
                danger
                :loading="acting[record.robot_id] === 'disable'"
                @click="act(record.robot_id, 'disable')"
              >禁用</a-button>
            </a-space>
          </template>
        </template>

        <template #expandedRowRender="{ record }">
          <a-empty
            v-if="!record.positions || record.positions.length === 0"
            description="空仓"
            :image="simpleImage"
          />
          <a-table
            v-else
            :data-source="record.positions"
            :columns="positionColumns"
            :pagination="false"
            :row-key="(p: RobotPosition) => p.ts_code"
            size="small"
          >
            <template #bodyCell="{ column: pcol, record: p }">
              <template v-if="pcol.key === 'ts_code'">
                <StockLink :code="p.ts_code" />
              </template>
              <template v-else-if="['cost_price','current_price'].includes(pcol.key)">
                {{ p[pcol.key] != null ? Number(p[pcol.key]).toFixed(3) : '-' }}
              </template>
              <template v-else-if="pcol.key === 'market_value'">
                {{ fmtAmount(p.market_value) }}
              </template>
              <template v-else-if="pcol.key === 'open_date'">
                {{ fmtDate(p.open_date) }}
              </template>
            </template>
          </a-table>
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { Empty, message } from 'ant-design-vue'
import {
  listRobots, pauseRobot, resumeRobot, enableRobot, disableRobot,
  type RobotSnapshot, type RobotPosition,
} from '@/api/robots'
import { fmtAmount, fmtPct, fmtDate } from '@/utils/format'
import StockLink from '@/components/StockLink.vue'

const simpleImage = Empty.PRESENTED_IMAGE_SIMPLE

const robots = ref<RobotSnapshot[]>([])
const loading = ref(false)
const acting = reactive<Record<number, string | null>>({})
const autoRefresh = ref(false)
let timer: number | null = null

const columns = [
  {
    title: 'ID', dataIndex: 'robot_id', key: 'robot_id', width: 80,
    sorter: (a: RobotSnapshot, b: RobotSnapshot) => a.robot_id - b.robot_id,
    defaultSortOrder: 'ascend' as const,
  },
  {
    title: '状态', dataIndex: 'state', key: 'state', width: 110,
    sorter: (a: RobotSnapshot, b: RobotSnapshot) => a.state.localeCompare(b.state),
  },
  {
    title: '灰度', dataIndex: 'enabled', key: 'enabled', width: 90,
    sorter: (a: RobotSnapshot, b: RobotSnapshot) =>
      Number(!!a.enabled) - Number(!!b.enabled),
  },
  {
    title: '现金', dataIndex: 'cash', key: 'cash', width: 140, align: 'right' as const,
    sorter: (a: RobotSnapshot, b: RobotSnapshot) => a.cash - b.cash,
  },
  {
    title: '总资产', dataIndex: 'total_assets', key: 'total_assets', width: 140, align: 'right' as const,
    sorter: (a: RobotSnapshot, b: RobotSnapshot) => a.total_assets - b.total_assets,
  },
  {
    title: '当日盈亏', dataIndex: 'daily_pnl_pct', key: 'daily_pnl_pct', width: 110, align: 'right' as const,
    sorter: (a: RobotSnapshot, b: RobotSnapshot) => (a.daily_pnl_pct ?? 0) - (b.daily_pnl_pct ?? 0),
  },
  {
    title: '持仓数', dataIndex: 'position_count', key: 'position_count', width: 90, align: 'right' as const,
    sorter: (a: RobotSnapshot, b: RobotSnapshot) => a.position_count - b.position_count,
  },
  { title: '操作', key: 'actions', width: 260 },
]

const positionColumns = [
  {
    title: '股票', dataIndex: 'ts_code', key: 'ts_code', width: 120,
    sorter: (a: RobotPosition, b: RobotPosition) => a.ts_code.localeCompare(b.ts_code),
  },
  {
    title: '持股', dataIndex: 'shares', key: 'shares', width: 100, align: 'right' as const,
    sorter: (a: RobotPosition, b: RobotPosition) => a.shares - b.shares,
  },
  {
    title: '成本价', dataIndex: 'cost_price', key: 'cost_price', width: 100, align: 'right' as const,
    sorter: (a: RobotPosition, b: RobotPosition) => (a.cost_price ?? 0) - (b.cost_price ?? 0),
  },
  {
    title: '现价', dataIndex: 'current_price', key: 'current_price', width: 100, align: 'right' as const,
    sorter: (a: RobotPosition, b: RobotPosition) => (a.current_price ?? 0) - (b.current_price ?? 0),
  },
  {
    title: '市值', dataIndex: 'market_value', key: 'market_value', width: 140, align: 'right' as const,
    sorter: (a: RobotPosition, b: RobotPosition) => (a.market_value ?? 0) - (b.market_value ?? 0),
  },
  {
    title: '建仓日', dataIndex: 'open_date', key: 'open_date', width: 120,
    sorter: (a: RobotPosition, b: RobotPosition) =>
      (a.open_date ?? '').localeCompare(b.open_date ?? ''),
  },
]

function stateColor(s: string) {
  return s === 'RUNNING' ? 'green' : s === 'PAUSED' ? 'orange' : 'default'
}

async function refresh() {
  loading.value = true
  try {
    const data = await listRobots()
    robots.value = data.robots ?? []
  } catch {
    /* toast 已处理 */
  } finally {
    loading.value = false
  }
}

const actionMap = {
  pause: pauseRobot,
  resume: resumeRobot,
  enable: enableRobot,
  disable: disableRobot,
} as const

async function act(id: number, action: keyof typeof actionMap) {
  acting[id] = action
  try {
    const { snapshot } = await actionMap[action](id)
    const i = robots.value.findIndex((r) => r.robot_id === id)
    if (i >= 0) robots.value[i] = snapshot
    message.success(`robot ${id} ${action} 成功`)
  } catch {
    /* toast 已处理 */
  } finally {
    acting[id] = null
  }
}

function startAuto() {
  if (timer != null) return
  timer = window.setInterval(refresh, 5000)
}
function stopAuto() {
  if (timer != null) { window.clearInterval(timer); timer = null }
}

import { watch } from 'vue'
watch(autoRefresh, (v) => { v ? startAuto() : stopAuto() })

onMounted(refresh)
onBeforeUnmount(stopAuto)
</script>

<style scoped>
.robots-page { padding: 12px; }
</style>
