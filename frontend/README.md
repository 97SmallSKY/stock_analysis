# LHB 前端 (W7)

Vue 3 + Vite + TypeScript + Pinia + Ant Design Vue + vue-router。

## 开发

```bash
cd frontend
npm install
npm run dev         # http://localhost:5173
```

Vite dev-server 把 `/api/*` 与 `/ws/*` 反代到 `http://localhost:8000` (uvicorn)。
在另一个终端启动后端:

```bash
uvicorn backend.api.main:app --reload --port 8000
```

## 构建

```bash
npm run build       # 产物 frontend/dist
npm run preview     # 本地预览构建产物
```

## 目录结构

- `src/main.ts`           — 入口, 安装 Pinia / router / AntD
- `src/App.vue`           — 顶层布局 + 顶部菜单
- `src/router/index.ts`   — 三个路由 `/`, `/lhb`, `/data-sources`
- `src/pages/*.vue`       — 页面 (W7-3/4/5 填充内容)
- `src/api/http.ts`       — axios 实例, 统一错误 toast

## 部署

见 `docs/DEPLOYMENT.md` 前端部分 (W7-6 交付): nginx 托管 `dist/`, 反代 `/api`/`/ws` 到 uvicorn。
