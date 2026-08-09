import request from './request'

/**
 * Classes API
 * Maps to backend:
 *   - class_mgmt: /api/v1/class_mgmt/classes/*
 *   - student_registry: /api/v1/student_registry/students
 */

export function getClasses(params?: {
  grade_id?: number
  page?: number
  page_size?: number
}) {
  return request.get('/class_mgmt/classes/', { params })
}

export function getClassDetail(classId: number) {
  return request.get(`/class_mgmt/classes/${classId}`)
}

export function getClassStudents(classId: number, params?: {
  page?: number
  page_size?: number
}) {
  return request.get(`/class_mgmt/classes/${classId}/students`, { params })
}

/** Fetch grades list (年级) */
export function getGrades() {
  return request.get('/grades')
}

/** Fetch students across classes (lightweight list for selectors) */
export function getStudents(params?: {
  grade_id?: number
  class_id?: number
  page?: number
  page_size?: number
}) {
  return request.get('/student_registry/students', { params })
}
