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
        manualChunks: {
          'vendor-vue': ['vue', 'vue-router', 'pinia'],
          'vendor-antd': ['ant-design-vue'],
          'vendor-grid': ['ag-grid-community', 'ag-grid-vue3'],
          'vendor-echarts': ['echarts', 'vue-echarts'],
        },
      },
    },
  },
})
