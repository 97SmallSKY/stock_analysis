<template>
  <a-layout class="app-layout">
    <a-layout-header class="app-header">
      <div class="logo">LHB 量化分析</div>
      <a-menu
        mode="horizontal"
        theme="dark"
        :selected-keys="[activeKey]"
        @click="onMenu"
        class="app-menu"
      >
        <a-menu-item key="/">Dashboard</a-menu-item>
        <a-menu-item key="/lhb">龙虎榜</a-menu-item>
        <a-menu-item key="/signals">决策信号</a-menu-item>
        <a-menu-item key="/lhb-pnl">盈亏分析</a-menu-item>
        <a-menu-item key="/backtest">策略对比</a-menu-item>
        <a-menu-item key="/robots">机器人</a-menu-item>
        <a-menu-item key="/reports">周报</a-menu-item>
        <a-menu-item key="/data-sources">数据源</a-menu-item>
      </a-menu>
    </a-layout-header>

    <a-layout-content class="app-content">
      <router-view />
    </a-layout-content>

    <a-layout-footer class="app-footer">
      <span>© LHB quant — 内部系统</span>
    </a-layout-footer>

    <!-- P7.2 全局股票下钻 Modal:日K → 周K → 分钟 K -->
    <StockDrillModal />
  </a-layout>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import StockDrillModal from '@/components/StockDrillModal.vue'

const route = useRoute()
const router = useRouter()

// 高亮当前顶层路由 (/xxx 取第一段)
const activeKey = computed(() => {
  const path = route.path
  if (path === '/' || path === '') return '/'
  const seg = path.split('/').filter(Boolean)[0]
  return seg ? `/${seg}` : '/'
})

function onMenu(info: { key: string }) {
  router.push(info.key)
}
</script>

<style>
html, body, #app { height: 100%; margin: 0; }
.app-layout { min-height: 100vh; }
.app-header {
  display: flex; align-items: center;
  padding: 0 24px;
}
.app-header .logo {
  color: #fff; font-weight: 600; font-size: 16px;
  margin-right: 32px; white-space: nowrap;
}
.app-menu { flex: 1; min-width: 0; }
.app-content { padding: 24px; background: #f5f5f5; }
.app-footer { text-align: center; color: #999; }
</style>
