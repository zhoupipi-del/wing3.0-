import axios, { type AxiosInstance, type InternalAxiosRequestConfig, type AxiosResponse } from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useUserStore } from '@/store/user'
import { formatValidationDetail, safeMessage } from '@/utils/errorFormat'

/**
 * Wings 3.0 Axios Interceptor Gateway
 *
 * - Request: auto-inject Authorization Bearer token from LocalStorage
 * - Response: centralized 401 (redirect to login), 403 (multi-tenant violation alert), 500 (server error)
 * - Timeout: 30s for AI prescription generation and heavy aggregation queries
 */

const service: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

/**
 * Request Interceptor — JWT Bearer Token Auto-Injection
 */
service.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const userStore = useUserStore()
    if (userStore.token) {
      config.headers.Authorization = `Bearer ${userStore.token}`
    }
    return config
  },
  (error: any) => {
    console.error('[Request Error]', error)
    return Promise.reject(error)
  }
)

/**
 * Response Interceptor — Centralized Error Handling
 *
 * 401 → clear auth + redirect to /login
 * 403 → multi-tenant violation alert (ElMessageBox)
 * 422 → 字段级可读校验错误（数组 detail 格式化，不再 [object Object]）
 * 500 → 通用文案，不展示内部对象（避免泄露 SQL / 路径 / 服务细节）
 * 其他 → 字符串原样（截断），非字符串给通用文案
 */
service.interceptors.response.use(
  (response: AxiosResponse) => {
    return response.data
  },
  (error: any) => {
    const { response } = error

    if (!response) {
      // Network error or timeout
      ElMessage.error('网络异常，请检查网络连接或稍后重试')
      return Promise.reject(error)
    }

    const status = response.status
    const rawDetail = response.data?.detail ?? response.data?.message

    switch (status) {
      case 401:
        // Token expired or invalid — clear auth and redirect
        ElMessage.error('登录已过期，请重新登录')
        const userStore = useUserStore()
        userStore.clearAuth()
        window.location.href = import.meta.env.BASE_URL + 'login'
        break

      case 403:
        // Multi-tenant isolation violation — show alert box
        ElMessageBox.alert(
          `多租户隔离拦截：${safeMessage(rawDetail)}`,
          '访问被拒绝',
          {
            confirmButtonText: '我知道了',
            type: 'warning',
          }
        )
        break

      case 404:
        ElMessage.error(`资源不存在：${safeMessage(rawDetail)}`)
        break

      case 422:
        // Validation error — show field-level readable errors (no [object Object])
        ElMessage.error(`参数校验失败：${formatValidationDetail(rawDetail)}`)
        break

      case 500:
        // 不展示内部错误对象，避免泄露 SQL / 路径 / 服务细节
        ElMessage.error('服务器内部错误，请稍后重试')
        console.error('[Server 500]', status)
        break

      default:
        ElMessage.error(safeMessage(rawDetail))
    }

    return Promise.reject(error)
  }
)

// ═══════════════════════════════════════════════════════════════
// Type-level override: the response interceptor (above) unpacks
// AxiosResponse → response.data at runtime.  We re-export a
// typed wrapper so that get/post/put/delete return Promise<R>
// directly — matching actual runtime behavior across every API
// caller in the project.
//
// BUILD-GATE-001: replaced `declare module 'axios'` augmentation
// (which was not being resolved by vue-tsc) with a precise type
// definition that supports the dual-generic <T, R> calling pattern
// used throughout the API layer.  No runtime behavior change.
// ═══════════════════════════════════════════════════════════════

/**
 * UnwrappedAxiosInstance — precise type for the axios instance
 * after the response interceptor strips the AxiosResponse wrapper.
 *
 * Dual-generic methods <T, R = T>:
 *   T = response body shape (for documentation)
 *   R = actual return type (defaults to T, matching interceptor behavior)
 *
 * This mirrors the original `declare module 'axios'` augmentation
 * that was not resolved by vue-tsc.
 */
interface UnwrappedAxiosInstance {
  <T = any, R = T>(config: any): Promise<R>
  get<T = any, R = T>(url: string, config?: any): Promise<R>
  delete<T = any, R = T>(url: string, config?: any): Promise<R>
  head<T = any, R = T>(url: string, config?: any): Promise<R>
  post<T = any, R = T>(url: string, data?: any, config?: any): Promise<R>
  put<T = any, R = T>(url: string, data?: any, config?: any): Promise<R>
  patch<T = any, R = T>(url: string, data?: any, config?: any): Promise<R>
  interceptors: AxiosInstance['interceptors']
  defaults: AxiosInstance['defaults']
}

/**
 * The runtime axios instance with interceptor-applied type override.
 * service already returns response.data via the response interceptor,
 * so we safely narrow the type from AxiosInstance to UnwrappedAxiosInstance.
 */
const request: UnwrappedAxiosInstance = service as unknown as UnwrappedAxiosInstance

export default request
