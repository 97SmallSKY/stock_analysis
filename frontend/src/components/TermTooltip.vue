<!--
  P19.7 (2026-04-20): 专业术语悬浮释义

  用法:
    <TermTooltip term="hot_money">游资</TermTooltip>
    <TermTooltip term="夏普比率" />     <!-- 无 slot 时用术语名自身 -->

  行为:
  - 文字右侧跟一个淡色 ⓘ 图标
  - 鼠标悬浮 500ms 显示 tooltip(brief + detail)
  - 点击 ⓘ 也能触发(移动端友好)
  - 术语字典里找不到时,仍显示文字,但不显示 ⓘ,避免误导

  依赖:`@/utils/glossary` 字典 + antd Tooltip
-->
<template>
  <span class="term-tooltip">
    <slot>{{ defaultText }}</slot>
    <a-tooltip v-if="term" :mouse-enter-delay="0.5" placement="top">
      <template #title>
        <div class="tt-title">
          <strong>{{ term.name }}</strong>
          <span class="tt-tag">{{ TAG_LABEL[term.tag] }}</span>
        </div>
        <div class="tt-brief">{{ term.brief }}</div>
        <div v-if="term.detail" class="tt-detail">{{ term.detail }}</div>
        <div v-if="term.example" class="tt-example">
          <em>例</em>:{{ term.example }}
        </div>
      </template>
      <QuestionCircleOutlined class="tt-icon" />
    </a-tooltip>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { QuestionCircleOutlined } from '@ant-design/icons-vue'
import { findTerm, type Term } from '@/utils/glossary'

const props = defineProps<{
  /** 术语 key、中文名或别名 */
  term: string
}>()

const term = computed<Term | null>(() => findTerm(props.term))
const defaultText = computed(() => term.value?.name ?? props.term)

const TAG_LABEL: Record<Term['tag'], string> = {
  lhb: '龙虎榜',
  score: '评分',
  sentiment: '情绪',
  backtest: '回测',
  market: '市场',
}
</script>

<style scoped>
.term-tooltip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.tt-icon {
  color: #bfbfbf;
  font-size: 12px;
  cursor: help;
  transition: color 0.2s;
}
.tt-icon:hover {
  color: #1677ff;
}
.tt-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.tt-tag {
  padding: 0 6px;
  font-size: 11px;
  color: #d4380d;
  background: rgba(212, 56, 13, 0.12);
  border-radius: 3px;
}
.tt-brief {
  font-size: 12px;
  line-height: 1.6;
}
.tt-detail {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.85);
}
.tt-example {
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.65);
}
.tt-example em {
  font-style: normal;
  color: #ffa940;
  margin-right: 4px;
}
</style>
