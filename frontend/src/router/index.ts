import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

// 路由表: W7-3/4/5 会逐步填充各页面实际内容
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
