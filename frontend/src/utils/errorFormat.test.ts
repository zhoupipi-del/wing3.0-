/**
 * errorFormat 安全格式化 — 最小自动化测试（W3-FE-ERROR-001）
 *
 * 运行：node --test --experimental-strip-types src/utils/errorFormat.test.ts
 * 覆盖 BOSS 要求的 8 项关闭门禁。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { formatValidationDetail, safeMessage } from './errorFormat.ts'

// 1. detail 为字符串 → 原样显示
test('string detail 原样显示', () => {
  assert.equal(formatValidationDetail('用户名不能为空'), '用户名不能为空')
  assert.equal(safeMessage('资源不存在'), '资源不存在')
})

// 2. detail 为 FastAPI 422 数组 → 「字段: 错误」
test('422 数组 → 字段: 错误', () => {
  const detail = [
    { loc: ['body', 'semester'], msg: 'field required', type: 'missing' },
    { loc: ['body', 'class_id'], msg: 'value is not a valid integer', type: 'type_error.integer' },
  ]
  assert.equal(
    formatValidationDetail(detail),
    'semester: field required；class_id: value is not a valid integer'
  )
})

// 3. loc 含 body/query/path → 去掉第一层来源前缀
test('loc 前缀 body/query/path 被去掉', () => {
  assert.equal(
    formatValidationDetail([{ loc: ['body', 'a'], msg: 'x' }]),
    'a: x'
  )
  assert.equal(
    formatValidationDetail([{ loc: ['query', 'b'], msg: 'y' }]),
    'b: y'
  )
  assert.equal(
    formatValidationDetail([{ loc: ['path', 'c'], msg: 'z' }]),
    'c: z'
  )
})

// 4. detail 为空 → 参数校验失败
test('空输入 → 参数校验失败', () => {
  assert.equal(formatValidationDetail([]), '参数校验失败')
  assert.equal(formatValidationDetail(null), '参数校验失败')
  assert.equal(formatValidationDetail(undefined), '参数校验失败')
  assert.equal(formatValidationDetail({}), '参数校验失败')
})

// 5. detail 为普通对象 → 不出现 [object Object]
test('普通对象 → 安全文案，不泄露 [object Object]', () => {
  const out = formatValidationDetail({ code: 500, exception: 'sql error', path: '/x' })
  assert.equal(out, '参数校验失败')
  assert.ok(!out.includes('[object Object]'))
  assert.ok(!out.includes('sql error'))
  assert.ok(!out.includes('exception'))
})

// 6. detail 为循环引用对象 → 不崩溃
test('循环引用对象 → 不崩溃', () => {
  const circular: any = { msg: 'boom' }
  circular.self = circular
  let out = ''
  assert.doesNotThrow(() => {
    out = formatValidationDetail(circular)
  })
  assert.equal(out, '参数校验失败')
})

// 7. 超长错误 → 被截断（422 数组 1000 / 字符串 500）
test('超长错误被截断', () => {
  assert.equal(formatValidationDetail('x'.repeat(2000)).length, 500)
  const longArray = Array.from({ length: 50 }, (_, i) => ({
    loc: ['body', `f${i}`],
    msg: 'y'.repeat(100),
  }))
  const out = formatValidationDetail(longArray)
  assert.ok(out.length <= 1000)
  assert.equal(out.length, 1000)
})

// 8. 500 对象错误 → 只显示通用文案（不泄露内部对象）
//    request.ts 的 500 分支使用常量字符串，不传 detail；
//    此处验证底层安全函数对对象输入返回通用文案。
test('500 对象错误 → 通用文案（无内部信息泄露）', () => {
  const out = safeMessage({ exception: 'sql syntax error', traceback: '...' })
  assert.equal(out, '请求失败，请稍后重试')
  assert.ok(!out.includes('[object Object]'))
  assert.ok(!out.includes('sql syntax error'))
  assert.ok(!out.includes('traceback'))
})
