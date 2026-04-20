import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// Vite 配置:
// - dev-server 将 /api 与 /ws 反代到后端 uvicorn (8000)
// - 生产环境由 nginx 反代,见 docs/DEPLOYMENT.md
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    target: 'es2020',
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // 手动分包: 把重库抽出, 降低主 chunk 体积
        //
        // STO-34 (2026-04-21) 调整:
        // - **ag-grid** 只有 LhbList.vue 一个页面在用(`grep -rln ag-grid src/`),
        //   从 manualChunks 移出后,rollup 自动把它放进 LhbList 的懒加载 chunk,
        //   首屏不再加载 ~1.1MB 的 vendor-grid。访问 LhbList 时才按需加载。
        // - **ant-design-vue / echarts**:保留集中分包,多页面共享,避免重复打包。
        //   后续若要进一步瘦身,可引入 `unplugin-vue-components` 做 antd 按需引入。
        manualChunks: {
          'vendor-vue': ['vue', 'vue-router', 'pinia'],
          'vendor-antd': ['ant-design-vue'],
          'vendor-echarts': ['echarts', 'vue-echarts'],
        },
      },
    },
  },
})
