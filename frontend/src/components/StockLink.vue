<template>
  <a class="stock-link" @click.stop="onClick">{{ code }}</a>
</template>

<script setup lang="ts">
import { useStockDrill } from '@/composables/useStockDrill'

const props = defineProps<{ code: string | null | undefined }>()
const drill = useStockDrill()

function onClick() {
  if (props.code) drill.open(String(props.code))
}
</script>

<style scoped>
.stock-link { color: #1677ff; cursor: pointer; }
.stock-link:hover { text-decoration: underline; }
</style>
