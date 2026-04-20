<template>
  <a-card :bordered="false" class="changelog-card">
    <template #title>
      <div class="cl-title">
        <HistoryOutlined />
        <span>{{ activeTab === 'changelog' ? '特性介绍' : '开发日志' }}</span>
        <a-tag color="blue" style="margin-left: 8px">共 {{ filtered.length }} / {{ entries.length }} 条</a-tag>
      </div>
    </template>

    <template #extra>
      <a-space>
        <a-checkbox-group v-model:value="activeTypes" :options="typeOptions" />
        <a-input-search
          v-model:value="keyword"
          placeholder="搜索标题/正文"
          style="width: 220px"
          allow-clear
        />
        <a-button size="small" :loading="loading" @click="load">
          <template #icon><ReloadOutlined /></template>
          刷新
        </a-button>
      </a-space>
    </template>

    <!-- P20.1 (2026-04-20): 拆分两个 tab,面向用户 vs 面向开发者 -->
    <a-tabs v-model:active-key="activeTab" @change="onTabChange" class="cl-tabs">
      <a-tab-pane key="changelog">
        <template #tab>
          <span><GiftOutlined /> 特性介绍</span>
        </template>
      </a-tab-pane>
      <a-tab-pane key="devlog">
        <template #tab>
          <span><CodeOutlined /> 开发日志</span>
        </template>
      </a-tab-pane>
    </a-tabs>

    <a-spin :spinning="loading">
      <a-alert
        v-if="error"
        type="error"
        :message="error"
        show-icon
        style="margin-bottom: 12px"
      />

      <a-empty v-if="!loading && filtered.length === 0" description="没有匹配的条目" />

      <a-timeline v-else mode="left">
        <a-timeline-item
          v-for="e in filtered"
          :key="e.date + '|' + e.title"
          :color="dotColorOf(e.type)"
        >
          <div class="cl-item">
            <div class="cl-item-head">
              <span class="cl-date">{{ e.date }}</span>
              <a-tag :color="typeColorOf(e.type)">{{ typeLabelOf(e.type) }}</a-tag>
              <span class="cl-title-text">{{ e.title }}</span>
            </div>
            <div class="cl-body" v-html="renderBody(e.body)" />
          </div>
        </a-timeline-item>
      </a-timeline>
    </a-spin>

    <div class="cl-footer">
      <a-typography-text type="secondary">
        <template v-if="activeTab === 'changelog'">
          面向用户 · 新特性 / 页面改动 / 影响使用体验的修复(<code>docs/CHANGELOG.md</code>)
        </template>
        <template v-else>
          面向开发者 · 代码重构 / 性能优化 / bug 根因 / 监控部署基建(<code>docs/DEVLOG.md</code>)
        </template>
      </a-typography-text>
    </div>

    <!-- 返回顶部:条目多时滚到底部回弹一键 -->
    <a-back-top :visibility-height="240" />
  </a-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  HistoryOutlined, ReloadOutlined, GiftOutlined, CodeOutlined,
} from '@ant-design/icons-vue'
import {
  fetchChangelog, fetchDevlog,
  CHANGELOG_TYPE_META,
  type ChangelogEntry,
} from '@/api/changelog'

const loading = ref(false)
const error = ref('')
const entries = ref<ChangelogEntry[]>([])
const activeTypes = ref<string[]>([])
const keyword = ref('')

// P20.1: tab 状态
type TabKey = 'changelog' | 'devlog'
const activeTab = ref<TabKey>('changelog')

const typeOptions = computed(() =>
  Array.from(new Set(entries.value.map((e) => e.type))).map((t) => ({
    label: typeLabelOf(t),
    value: t,
  })),
)

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const tset = new Set(activeTypes.value)
  return entries.value.filter((e) => {
    if (tset.size > 0 && !tset.has(e.type)) return false
    if (!kw) return true
    return (
      e.title.toLowerCase().includes(kw) ||
      e.body.toLowerCase().includes(kw) ||
      e.date.includes(kw)
    )
  })
})

function typeLabelOf(t: string) {
  return CHANGELOG_TYPE_META[t]?.label ?? t
}
function typeColorOf(t: string) {
  return CHANGELOG_TYPE_META[t]?.color ?? 'default'
}
function dotColorOf(t: string) {
  const m: Record<string, string> = {
    feature: 'blue', fix: 'red', ui: 'purple',
    refactor: 'purple', perf: 'green',
    devops: 'orange', docs: 'gray', ops: 'orange',
  }
  return m[t] ?? 'gray'
}

function renderBody(md: string): string {
  if (!md) return ''
  const esc = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

  const lines = md.split(/\r?\n/)
  const out: string[] = []
  let inList = false
  const flushList = () => {
    if (inList) { out.push('</ul>'); inList = false }
  }

  for (const raw of lines) {
    const line = raw.trimEnd()
    if (/^\s*[-*]\s+/.test(line)) {
      if (!inList) { out.push('<ul>'); inList = true }
      out.push('<li>' + inline(line.replace(/^\s*[-*]\s+/, '')) + '</li>')
    } else if (line.trim() === '') {
      flushList()
      out.push('')
    } else {
      flushList()
      out.push('<p>' + inline(line) + '</p>')
    }
  }
  flushList()
  return out.join('\n')

  function inline(s: string): string {
    return esc(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = activeTab.value === 'devlog'
      ? await fetchDevlog()
      : await fetchChangelog()
    entries.value = data.entries || []
    if (data.error) error.value = data.error
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function onTabChange() {
  // 切 tab 时重置筛选与关键字,避免跨数据源残留
  activeTypes.value = []
  keyword.value = ''
  load()
}

onMounted(load)
</script>

<style scoped>
.changelog-card {
  max-width: 1000px;
  margin: 0 auto;
}
.cl-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.cl-tabs {
  margin: -4px 0 8px;
}
.cl-item {
  padding-bottom: 4px;
}
.cl-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.cl-date {
  font-family: 'SF Mono', Menlo, monospace;
  font-size: 12px;
  color: #8c8c8c;
}
.cl-title-text {
  font-weight: 600;
  color: #262626;
}
.cl-body {
  margin-top: 6px;
  font-size: 13px;
  line-height: 1.7;
  color: #595959;
}
.cl-body :deep(code) {
  background: #f5f5f5;
  padding: 1px 4px;
  border-radius: 3px;
  font-family: 'SF Mono', Menlo, monospace;
  font-size: 12px;
}
.cl-body :deep(ul) {
  margin: 4px 0;
  padding-left: 20px;
}
.cl-footer {
  margin-top: 16px;
  padding: 8px 0;
  border-top: 1px dashed #f0f0f0;
  text-align: center;
  font-size: 12px;
}
</style>
