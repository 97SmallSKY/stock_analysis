<template>
  <a-card :bordered="false" class="changelog-card">
    <template #title>
      <div class="cl-title">
        <HistoryOutlined />
        <span>特性介绍 · 开发日志</span>
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
        <a-button :icon="h(ReloadOutlined)" size="small" :loading="loading" @click="load">
          刷新
        </a-button>
      </a-space>
    </template>

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
        数据源:<code>docs/CHANGELOG.md</code> · 每次优化完成后追加条目(时间倒序)
      </a-typography-text>
    </div>
  </a-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, h } from 'vue'
import { HistoryOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { fetchChangelog, CHANGELOG_TYPE_META, type ChangelogEntry } from '@/api/changelog'

const loading = ref(false)
const error = ref('')
const entries = ref<ChangelogEntry[]>([])
const activeTypes = ref<string[]>([])
const keyword = ref('')

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
  // antdv timeline 的 color 只能吃颜色词或 hex
  const m: Record<string, string> = {
    feature: 'blue', fix: 'red', refactor: 'purple',
    perf: 'green', docs: 'gray', ops: 'orange',
  }
  return m[t] ?? 'gray'
}

/**
 * 极简 markdown 渲染(只处理列表/加粗/代码/段落)
 * 不引入 markdown-it,避免前端额外依赖;CHANGELOG 条目结构本就简单。
 */
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
    const data = await fetchChangelog()
    entries.value = data.entries || []
    if (data.error) error.value = data.error
  } catch (e: any) {
    error.value = e?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.changelog-card {
  max-width: 1080px;
  margin: 0 auto;
}
.cl-title {
  display: inline-flex; align-items: center; gap: 8px;
  font-weight: 600;
}
.cl-item-head {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 6px;
}
.cl-date {
  color: #8c8c8c; font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 12px;
}
.cl-title-text { font-weight: 600; color: #262626; }
.cl-body {
  color: #595959; font-size: 13px; line-height: 1.8;
  background: #fafbfc; border-radius: 6px; padding: 8px 12px;
}
.cl-body :deep(code) {
  background: #f0f0f0; padding: 1px 6px; border-radius: 4px;
  font-size: 12px; color: #d4380d;
}
.cl-body :deep(ul) { margin: 4px 0 4px 18px; padding: 0; }
.cl-body :deep(li) { margin: 2px 0; }
.cl-body :deep(p) { margin: 4px 0; }
.cl-footer {
  margin-top: 18px; text-align: center; font-size: 12px;
}
</style>
