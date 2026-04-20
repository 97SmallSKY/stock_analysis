<!--
  P19.5 (2026-04-20): 日期范围快捷选择器

  与 `utils/date_range.ts` 配合使用。

  用法:
    <DateRangeQuickPick v-model="range" :options="['1y','3m','1m']" />

    // range 为 { start: 'YYYY-MM-DD', end: 'YYYY-MM-DD' }

  事件语义(符合用户需求 2026-04-20):
    - 点快捷钮改「开始日期」,end 保持当前值(默认今天)
    - 用户手动编辑开始/结束 -> 同步 v-model

  默认快捷钮:1年 / 季度 / 3个月 / 1个月 / 1周 / 5日 / 今天
-->
<template>
  <a-space :size="8" wrap>
    <a-range-picker
      v-model:value="pickerValue"
      value-format="YYYY-MM-DD"
      size="small"
      :allow-clear="false"
      :disabled-date="disabledDate"
      @change="onPickerChange"
    />
    <a-button
      v-for="opt in activeOptions"
      :key="opt.key"
      size="small"
      :type="opt.key === activeKey ? 'primary' : 'default'"
      @click="applyQuick(opt.key)"
    >
      {{ opt.label }}
    </a-button>
  </a-space>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  calcRange,
  defaultRange,
  DEFAULT_QUICK_OPTIONS,
  type DateRange,
  type QuickRange,
} from '@/utils/date_range'

const props = withDefaults(defineProps<{
  modelValue?: DateRange
  /** 仅展示这些快捷钮(默认全量 8 个) */
  options?: QuickRange[]
  /** 日期选择器 max 限制(ISO),默认无限制 */
  maxDate?: string
  /** 日期选择器 min 限制(ISO),默认无限制 */
  minDate?: string
}>(), {
  modelValue: () => defaultRange(),
  options: undefined,
  maxDate: undefined,
  minDate: undefined,
})

const emit = defineEmits<{
  (e: 'update:modelValue', v: DateRange): void
  (e: 'change', v: DateRange): void
}>()

// 上次应用的快捷钮 key,用于 UI 高亮
const activeKey = ref<QuickRange | null>(null)

// antd DatePicker 需要 [start, end] 数组
const pickerValue = ref<[string, string]>([
  props.modelValue.start,
  props.modelValue.end,
])

watch(() => props.modelValue, (v) => {
  if (!v) return
  if (pickerValue.value[0] !== v.start || pickerValue.value[1] !== v.end) {
    pickerValue.value = [v.start, v.end]
  }
})

const activeOptions = computed(() => {
  if (!props.options) return DEFAULT_QUICK_OPTIONS
  const keys = new Set(props.options)
  return DEFAULT_QUICK_OPTIONS.filter((o) => keys.has(o.key))
})

function disabledDate(cur: { valueOf: () => number }) {
  if (!cur) return false
  const t = cur.valueOf()
  if (props.minDate) {
    const min = new Date(props.minDate + 'T00:00:00').valueOf()
    if (t < min) return true
  }
  if (props.maxDate) {
    const max = new Date(props.maxDate + 'T23:59:59').valueOf()
    if (t > max) return true
  }
  return false
}

function applyQuick(key: QuickRange) {
  activeKey.value = key
  // 符合用户约定:快捷钮改开始,end 保持 = 当前 modelValue.end(若用户编辑过)
  // 若 modelValue.end 超过 maxDate 就回落到 maxDate
  const anchorEnd = props.maxDate && props.modelValue.end > props.maxDate
    ? props.maxDate
    : props.modelValue.end
  const r = calcRange(key, anchorEnd)
  // start 不能早于 minDate
  if (props.minDate && r.start < props.minDate) {
    r.start = props.minDate
  }
  pickerValue.value = [r.start, r.end]
  emit('update:modelValue', r)
  emit('change', r)
}

function onPickerChange(val: [string, string] | null) {
  // 手动编辑 -> 清除快捷钮高亮
  activeKey.value = null
  if (!val || !val[0] || !val[1]) return
  const r: DateRange = { start: val[0], end: val[1] }
  emit('update:modelValue', r)
  emit('change', r)
}
</script>
