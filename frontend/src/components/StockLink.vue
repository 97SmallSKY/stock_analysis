<template>
  <a class="stock-link" :title="full" @click.stop="onClick">
    <span class="code">{{ code }}</span>
    <span v-if="name" class="name">{{ name }}</span>
  </a>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useStockDrill } from '@/composables/useStockDrill'

const props = defineProps<{
  code: string | null | undefined
  name?: string | null | undefined
}>()
const drill = useStockDrill()

const full = computed(() => (props.name ? `${props.code} ${props.name}` : props.code ?? ''))

function onClick() {
  if (props.code) drill.open(String(props.code))
}
</script>

<style scoped>
.stock-link {
  color: #1677ff; cursor: pointer;
  display: inline-flex; align-items: baseline; gap: 6px;
  white-space: nowrap;
}
.stock-link:hover { text-decoration: underline; }
.stock-link .code {
  font-family: 'SF Mono', Menlo, monospace;
  font-size: 12px;
}
.stock-link .name {
  color: #595959;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.2px;
}
</style>
