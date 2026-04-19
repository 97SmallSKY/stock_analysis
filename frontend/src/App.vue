<template>
  <a-config-provider :theme="themeConfig" :locale="zhCN">
    <a-layout class="app-layout">
      <a-layout-header class="app-header">
        <div class="logo">
          <StockOutlined class="logo-icon" />
          <span class="logo-text">LHB <em>量化</em></span>
        </div>
        <a-menu
          mode="horizontal"
          theme="dark"
          :selected-keys="[activeKey]"
          @click="onMenu"
          class="app-menu"
        >
          <a-menu-item key="/">
            <template #icon><DashboardOutlined /></template>
            Dashboard
          </a-menu-item>
          <a-menu-item key="/lhb">
            <template #icon><TableOutlined /></template>
            龙虎榜
          </a-menu-item>
          <a-menu-item key="/signals">
            <template #icon><BulbOutlined /></template>
            决策信号
          </a-menu-item>
          <a-menu-item key="/lhb-pnl">
            <template #icon><PieChartOutlined /></template>
            盈亏分析
          </a-menu-item>
          <a-menu-item key="/backtest">
            <template #icon><LineChartOutlined /></template>
            策略对比
          </a-menu-item>
          <a-menu-item key="/backtest-run">
            <template #icon><LineChartOutlined /></template>
            回测任务
          </a-menu-item>
          <a-menu-item key="/robots">
            <template #icon><RobotOutlined /></template>
            机器人
          </a-menu-item>
          <a-menu-item key="/reports">
            <template #icon><FileTextOutlined /></template>
            周报
          </a-menu-item>
          <a-menu-item key="/data-sources">
            <template #icon><DatabaseOutlined /></template>
            数据源
          </a-menu-item>
        </a-menu>
      </a-layout-header>

      <a-layout-content class="app-content">
        <router-view v-slot="{ Component }">
          <transition name="fade-slide" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </a-layout-content>

      <a-layout-footer class="app-footer">
        <span>© LHB quant — 内部系统</span>
      </a-layout-footer>

      <!-- P7.2 全局股票下钻 Modal:日K → 周K → 分钟 K -->
      <StockDrillModal />
    </a-layout>
  </a-config-provider>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { theme } from 'ant-design-vue'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import {
  DashboardOutlined, TableOutlined, BulbOutlined, PieChartOutlined,
  LineChartOutlined, RobotOutlined, FileTextOutlined, DatabaseOutlined,
  StockOutlined,
} from '@ant-design/icons-vue'
import StockDrillModal from '@/components/StockDrillModal.vue'

const route = useRoute()
const router = useRouter()

// P8.2 全局 Theme Token: 金融红主色 + 柔和圆角 + 细腻边框
const themeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: '#d4380d',       // 金融红
    colorInfo: '#1677ff',
    colorSuccess: '#389e0d',
    colorWarning: '#faad14',
    colorError: '#cf1322',
    borderRadius: 8,
    colorBorder: '#e7e7e7',
    fontSize: 13,
    wireframe: false,
  },
  components: {
    Layout: {
      headerBg: 'linear-gradient(90deg, #0f1f3d 0%, #17325b 100%)',
      headerHeight: 56,
      headerPadding: '0 24px',
      footerBg: 'transparent',
    },
    Menu: {
      darkItemBg: 'transparent',
      darkItemSelectedBg: 'rgba(212, 56, 13, 0.18)',
      darkItemHoverBg: 'rgba(255, 255, 255, 0.08)',
    },
    Card: {
      headerBg: 'transparent',
      headerFontSize: 14,
    },
    Table: {
      headerBg: '#fafafa',
      headerSplitColor: 'transparent',
      rowHoverBg: '#fff7f5',
    },
    Tag: { borderRadiusSM: 4 },
  },
}

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
body {
  background: #f5f7fa;
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei',
    'Helvetica Neue', Arial, sans-serif;
}
.app-layout {
  min-height: 100vh;
  background: transparent;
}
.app-header {
  display: flex; align-items: center;
  /* 渐变由 theme token.Layout.headerBg 提供; 这里只管 padding/shadow */
  box-shadow: 0 2px 8px rgba(15, 31, 61, 0.16);
  position: sticky; top: 0; z-index: 10;
}
.app-header .logo {
  display: flex; align-items: center;
  color: #fff; font-weight: 600; font-size: 16px;
  margin-right: 32px; white-space: nowrap;
  letter-spacing: 0.5px;
}
.app-header .logo-icon {
  font-size: 22px; color: #ff7a45; margin-right: 8px;
}
.app-header .logo-text em {
  font-style: normal; color: #ffa940; font-weight: 400; margin-left: 4px;
}
.app-menu {
  flex: 1; min-width: 0;
  background: transparent !important;
  border-bottom: none !important;
}
.app-content {
  padding: 20px 24px;
  background: transparent;
}
.app-footer {
  text-align: center;
  color: #bfbfbf;
  padding: 12px 24px;
  font-size: 12px;
}

/* 全局卡片质感 */
.ant-card {
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  transition: box-shadow .24s ease;
}
.ant-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
}

/* 表格斑马纹 */
.ant-table-tbody > tr:nth-child(even) > td {
  background: #fafbfc;
}
.ant-table-tbody > tr:hover > td {
  background: #fff7f5 !important;
}

/* 路由切换过渡 */
.fade-slide-enter-active, .fade-slide-leave-active {
  transition: opacity .18s ease, transform .18s ease;
}
.fade-slide-enter-from { opacity: 0; transform: translateY(4px); }
.fade-slide-leave-to   { opacity: 0; transform: translateY(-4px); }

/* AntdV Form inline 筛选区统一密度 */
.ant-form-inline .ant-form-item { margin-bottom: 0; margin-inline-end: 12px; }
.ant-card + .ant-card,
.ant-row + .ant-row { margin-top: 12px; }

/* 表格表头更清晰 */
.ant-table-thead > tr > th {
  font-weight: 600 !important;
  color: #333 !important;
}

/* 表格排序箭头激活色 */
.ant-table-column-sorter-up.active,
.ant-table-column-sorter-down.active { color: #d4380d !important; }

/* Tag 胶囊化 */
.ant-tag { border-radius: 10px; padding-inline: 8px; }

/* 菜单底色 */
.ant-menu-dark.ant-menu-horizontal > .ant-menu-item {
  border-radius: 6px; margin: 0 2px;
}

/* 空态文字居中柔和 */
.ant-empty-description { color: #bfbfbf; }
</style>
