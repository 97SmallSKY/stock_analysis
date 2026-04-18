import axios, { type AxiosInstance } from 'axios'
import { message } from 'ant-design-vue'

// 统一 axios 实例
// - baseURL 走相对路径, dev 由 vite proxy 转发, prod 由 nginx 反代
// - 10s 超时,与后端 /api/dashboard 等接口量级匹配
const http: AxiosInstance = axios.create({
  baseURL: '/',
  timeout: 10_000,
})

http.interceptors.response.use(
  (resp) => resp,
  (err) => {
    // 后端 FastAPI 错误体: { detail: "..." }
    const detail =
      err?.response?.data?.detail ??
      err?.response?.statusText ??
      err?.message ??
      '网络异常'
    // 仅 toast 非 404 错误 (404 让业务代码自行处理)
    if (err?.response?.status !== 404) {
      message.error(String(detail))
    }
    return Promise.reject(err)
  },
)

export default http
