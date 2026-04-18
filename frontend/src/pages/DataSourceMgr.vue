<template>
  <div class="ds-page">
    <a-card :bordered="false">
      <template #title>
        <a-space>
          <span>数据源管理</span>
          <a-tag color="blue">{{ status?.adapters?.length ?? 0 }} 个适配器</a-tag>
        </a-space>
      </template>
      <template #extra>
        <a-space>
          <a-switch v-model:checked="autoRefresh" checked-children="自动" un-checked-children="手动" />
          <a-button size="small" :loading="store.loading" @click="store.load()">刷新</a-button>
        </a-space>
      </template>

      <a-table
        :data-source="status?.adapters ?? []"
        :columns="adapterCols"
        :row-key="(r: any) => r.name"
        :pagination="false"
        :loading="store.loading && !status"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'health'">
            <a-space>
              <span class="dot" :style="{ background: healthColor(record.last_health?.is_healthy) }" />
              <span>
                {{ record.last_health?.is_healthy === true ? '健康'
                 : record.last_health?.is_healthy === false ? '异常'
                 : '未探针' }}
              </span>
              <span class="sub">
                {{ record.last_health?.latency_ms != null ? record.last_health.latency_ms + ' ms' : '-' }}
              </span>
            </a-space>
          </template>
          <template v-else-if="column.key === 'circuit'">
            <a-tag :color="cbColor(record.circuit?.state)">
              {{ record.circuit?.state || '-' }}
            </a-tag>
            <span v-if="record.circuit?.failure_count" class="sub">
              failures: {{ record.circuit.failure_count }}
            </span>
          </template>
          <template v-else-if="column.key === 'initialized'">
            <a-tag :color="record.initialized ? 'green' : 'red'">
              {{ record.initialized ? '就绪' : '未就绪' }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'actions'">
            <a-space>
              <a-button size="small" @click="onRetry(record.name)">重试 init</a-button>
              <a-button
                size="small"
                :disabled="record.circuit?.state === 'closed'"
                @click="onCircuit(record.name, 'close')"
              >
                关闭熔断
              </a-button>
              <a-button
                size="small"
                danger
                :disabled="record.circuit?.state === 'open'"
                @click="onCircuit(record.name, 'open')"
              >
                手动熔断
              </a-button>
            </a-space>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-card :bordered="false" style="margin-top: 12px;" title="优先级链 (拖拽调整顺序)">
      <a-empty v-if="!chainList.length" description="暂无优先级链" />
      <div v-for="entry in chainList" :key="entry.category" class="chain-row">
        <div class="chain-label">{{ entry.category }}</div>
        <div class="chain-chips">
          <div
            v-for="(name, idx) in entry.chain"
            :key="name"
            class="chip"
            :class="{ dragging: dragging.category === entry.category && dragging.from === idx }"
            draggable="true"
            @dragstart="onDragStart(entry.category, idx)"
            @dragover.prevent="onDragOver(entry.category, idx)"
            @drop.prevent="onDrop(entry.category, idx)"
            @dragend="onDragEnd"
          >
            <span class="rank">{{ idx + 1 }}</span>
            <span class="name">{{ name }}</span>
          </div>
        </div>
        <a-button
          size="small"
          type="link"
          :disabled="!isDirty(entry.category)"
          @click="resetChain(entry.category)"
        >
          撤销
        </a-button>
      </div>
      <a-alert
        v-if="anyDirty"
        type="warning"
        show-icon
        style="margin-top: 8px;"
        message="有未保存的顺序变更"
      >
        <template #action>
          <a-space>
            <a-button size="small" @click="resetAll">全部撤销</a-button>
            <a-button size="small" type="primary" :loading="saving" @click="saveAll">保存</a-button>
          </a-space>
        </template>
      </a-alert>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useDataSourceStore } from '@/stores/data_sources'
import { healthColor, cbColor } from '@/utils/format'

const store = useDataSourceStore()
const status = computed(() => store.status)

const adapterCols = [
  { title: '适配器', dataIndex: 'name', key: 'name', width: 140 },
  { title: '状态', key: 'initialized', width: 90 },
  { title: '健康', key: 'health', width: 220 },
  { title: '熔断', key: 'circuit', width: 180 },
  { title: '操作', key: 'actions' },
]

// 优先级拖拽本地编辑态: 拷贝一份 priority_chains, 与 server 值对比算 dirty
const editChains = ref<Record<string, string[]>>({})
const saving = ref(false)

function syncFromServer() {
  const src = status.value?.priority_chains ?? {}
  editChains.value = Object.fromEntries(
    Object.entries(src).map(([k, v]) => [k, [...v]]),
  )
}

watch(() => status.value?.priority_chains, syncFromServer, { deep: true })

const chainList = computed(() =>
  Object.entries(editChains.value).map(([category, chain]) => ({ category, chain })),
)

function isDirty(category: string): boolean {
  const server = status.value?.priority_chains?.[category] ?? []
  const cur = editChains.value[category] ?? []
  return server.length !== cur.length || server.some((v, i) => v !== cur[i])
}

const anyDirty = computed(() =>
  Object.keys(editChains.value).some(isDirty),
)

function resetChain(category: string) {
  const server = status.value?.priority_chains?.[category] ?? []
  editChains.value[category] = [...server]
}

function resetAll() {
  syncFromServer()
}

// ---- 拖拽 ----
const dragging = reactive<{ category: string | null; from: number }>({
  category: null, from: -1,
})

function onDragStart(category: string, idx: number) {
  dragging.category = category
  dragging.from = idx
}

function onDragOver(category: string, _idx: number) {
  // 禁止跨 category 拖拽: 只有同一 category 才响应 drop
  if (dragging.category !== category) return
}

function onDrop(category: string, to: number) {
  if (dragging.category !== category) return
  const from = dragging.from
  if (from < 0 || from === to) return
  const arr = [...editChains.value[category]]
  const [x] = arr.splice(from, 1)
  arr.splice(to, 0, x)
  editChains.value[category] = arr
}

function onDragEnd() {
  dragging.category = null
  dragging.from = -1
}

// ---- 保存 ----
async function saveAll() {
  saving.value = true
  try {
    const tasks: Promise<unknown>[] = []
    for (const [cat, chain] of Object.entries(editChains.value)) {
      if (isDirty(cat)) tasks.push(store.applyPriority(cat, chain))
    }
    await Promise.all(tasks)
    message.success(`已保存 ${tasks.length} 个优先级链`)
    await store.load()
  } catch {
    // http 拦截器已 toast
  } finally {
    saving.value = false
  }
}

// ---- 适配器动作 ----
async function onRetry(name: string) {
  try {
    const r = await store.retry(name)
    if (r?.initialized) message.success(`${name} 重新初始化成功`)
    else message.warning(`${name} 初始化失败`)
  } catch { /* toast 已处理 */ }
}

async function onCircuit(name: string, action: 'open' | 'close') {
  try {
    await store.forceCircuit(name, action)
    message.success(`${name} 熔断 ${action === 'close' ? '已关闭' : '已打开'}`)
  } catch { /* toast 已处理 */ }
}

// ---- 自动刷新 (每 10s 拉一次, 仅当 autoRefresh 打开) ----
const autoRefresh = ref(true)
let timer: number | null = null
function tick() {
  if (autoRefresh.value && !saving.value && !anyDirty.value) {
    store.load().catch(() => {})
  }
  timer = window.setTimeout(tick, 10_000)
}

onMounted(() => {
  store.load().catch(() => {})
  timer = window.setTimeout(tick, 10_000)
})
onUnmounted(() => {
  if (timer !== null) window.clearTimeout(timer)
})
</script>

<style scoped>
.ds-page .dot {
  display: inline-block; width: 10px; height: 10px; border-radius: 50%;
}
.ds-page .sub { color: #8c8c8c; font-size: 12px; }

.chain-row {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 0; border-bottom: 1px dashed #f0f0f0;
}
.chain-row:last-child { border-bottom: none; }
.chain-label {
  width: 180px; font-family: monospace; color: #595959;
}
.chain-chips {
  display: flex; flex: 1; gap: 8px; flex-wrap: wrap;
  padding: 4px; min-height: 36px;
}
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px;
  background: #fafafa; border: 1px solid #d9d9d9; border-radius: 14px;
  cursor: grab; user-select: none;
}
.chip:hover { background: #e6f4ff; border-color: #91caff; }
.chip.dragging { opacity: 0.4; }
.chip .rank {
  display: inline-block; min-width: 18px; text-align: center;
  background: #1677ff; color: #fff; border-radius: 50%;
  font-size: 11px; line-height: 18px;
}
.chip .name { font-family: monospace; }
</style>
