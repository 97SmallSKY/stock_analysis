import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'dashboard',
    component: () => import('@/pages/Dashboard.vue'),
    meta: { title: 'Dashboard' },
  },
  {
    path: '/lhb',
    name: 'lhb',
    component: () => import('@/pages/LhbList.vue'),
    meta: { title: '龙虎榜' },
  },
  {
    path: '/data-sources',
    name: 'data-sources',
    component: () => import('@/pages/DataSourceMgr.vue'),
    meta: { title: '数据源管理' },
  },
  {
    path: '/backtest',
    name: 'backtest',
    component: () => import('@/pages/Backtest.vue'),
    meta: { title: '策略对比' },
  },
  {
    path: '/reports',
    name: 'reports',
    component: () => import('@/pages/Reports.vue'),
    meta: { title: '周报' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  const t = to.meta?.title as string | undefined
  if (t) document.title = `${t} · LHB 量化分析`
})

export default router
