/**
 * 安全错误格式化工具（纯函数，无副作用，独立可测）
 *
 * 被 src/api/request.ts 的响应拦截器复用。
 *
 * 安全边界（W3-FE-ERROR-001）：
 * - 生产环境禁止把 FastAPI 500 / 未知错误对象直接 JSON.stringify 展示给用户，
 *   否则可能泄露内部异常、SQL 字段、路径或服务细节。
 * - 422 的结构化字段错误数组 [{loc, msg, type}] 可格式化展示为「字段: 错误」；
 *   但对象 / 循环引用 / null 一律回退到通用文案，绝不序列化原始对象。
 */

/** 422 字段校验错误格式化：数组 → 可读「字段: 信息」；其余安全回退 */
export function formatValidationDetail(raw: unknown): string {
  if (typeof raw === 'string') {
    return raw.slice(0, 500)
  }

  if (Array.isArray(raw)) {
    const message = raw
      .map((item: unknown) => {
        if (!item || typeof item !== 'object') return ''

        const error = item as { loc?: unknown; msg?: unknown }

        const location = Array.isArray(error.loc)
          ? error.loc
              .slice(1)
              .filter(
                (part): part is string | number =>
                  typeof part === 'string' || typeof part === 'number'
              )
              .join('.')
          : ''

        const detail =
          typeof error.msg === 'string' ? error.msg : '参数校验失败'

        return location ? `${location}: ${detail}` : detail
      })
      .filter(Boolean)
      .join('；')

    return message.slice(0, 1000) || '参数校验失败'
  }

  // 未知对象 / 循环引用 / null / undefined：不 JSON.stringify，避免泄露内部信息
  return '参数校验失败'
}

/** 非 422 错误的安全文案：字符串原样（截断），非字符串给通用文案 */
export function safeMessage(raw: unknown): string {
  return typeof raw === 'string' ? raw.slice(0, 500) : '请求失败，请稍后重试'
}
