import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useUserStore } from '@/store/user'
import type { UserRole } from '@/types'

/**
 * RBAC Dynamic Route Guards
 *
 * Routes are organized by business domain, 1:1 aligned with backend modules.
 * Role-based access control: MS_ADMIN / GRADE_LEADER / CLASS_TEACHER / PARENT
 */

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    component: () => import('@/layouts/AuthLayout.vue'),
    children: [
      {
        path: '',
        name: 'Login',
        component: () => import('@/views/Login.vue'),
        meta: { title: '登录', public: true },
      },
    ],
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      // 多校区大数据指挥舱 (默认首页)
      {
        path: 'dashboard',
        name: 'DashboardOverview',
        component: () => import('@/views/dashboard/DashboardOverview.vue'),
        meta: {
          title: '指挥舱看板',
          icon: 'DataLine',
          roles: ['MS_ADMIN', 'GRADE_LEADER'] as UserRole[],
        },
      },
      // RDI 风险雷达
      {
        path: 'rdi-radar',
        name: 'RdiRadar',
        component: () => import('@/views/rdi-radar/Index.vue'),
        meta: {
          title: 'RDI 风险雷达',
          icon: 'Monitor',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // RDI 四维风险看板 (心理危机分布图)
      {
        path: 'rdi-dashboard',
        name: 'RdiDashboard',
        component: () => import('@/views/rdi-radar/RdiDashboard.vue'),
        meta: {
          title: 'RDI 风险看板',
          icon: 'Odometer',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // 审批工作台
      {
        path: 'approval-center',
        name: 'ApprovalCenter',
        component: () => import('@/views/approval-center/Index.vue'),
        meta: {
          title: '审批工作台',
          icon: 'Checked',
          roles: ['MS_ADMIN', 'GRADE_LEADER'] as UserRole[],
        },
      },
      // 德育与处分中心
      {
        path: 'behavior',
        name: 'BehaviorCenter',
        component: () => import('@/views/behavior/Index.vue'),
        meta: {
          title: '德育与处分中心',
          icon: 'Warning',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // 惩戒流转中心
      {
        path: 'discipline',
        name: 'DisciplineCenter',
        component: () => import('@/views/discipline/DisciplineCenter.vue'),
        meta: {
          title: '惩戒流转中心',
          icon: 'Stamp',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // AI 德育处方
      {
        path: 'ai-prescription',
        name: 'AiPrescription',
        component: () => import('@/views/ai-prescription/Index.vue'),
        meta: {
          title: 'AI 德育处方',
          icon: 'MagicStick',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // 审题助手 — 数学题翻译引擎
      {
        path: 'teach-math/coach',
        name: 'StudentCoach',
        component: () => import('@/views/teach-math/StudentCoach.vue'),
        meta: {
          title: '审题助手',
          icon: 'Reading',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // 审题诊断 — 教师端学情仪表盘
      {
        path: 'teach-math/report',
        name: 'TeacherReport',
        component: () => import('@/views/teach-math/TeacherReport.vue'),
        meta: {
          title: '审题诊断',
          icon: 'DataAnalysis',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // 教研协同指挥台 — 集体备课+听课评课+教研活动
      {
        path: 'research',
        name: 'ResearchConsole',
        component: () => import('@/views/research/Index.vue'),
        meta: {
          title: '教研协同',
          icon: 'Coordinate',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // 作业管理 — 结构化作业+批改+错题标记
      {
        path: 'homework',
        name: 'HomeworkConsole',
        component: () => import('@/views/homework/Index.vue'),
        meta: {
          title: '作业管理',
          icon: 'EditPen',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // 错题断层漏斗 — 错题本+知识点断层+AI处方
      {
        path: 'error-funnel',
        name: 'ErrorFunnel',
        component: () => import('@/views/error-funnel/Index.vue'),
        meta: {
          title: '错题断层',
          icon: 'Filter',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // ── 新增模块路由 (Phase A 割接) ──
      // 素质评价仪表盘
      {
        path: 'evaluation',
        name: 'EvaluationDashboard',
        component: () => import('@/views/evaluation/Index.vue'),
        meta: {
          title: '素质评价',
          icon: 'TrendCharts',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // 正向加分录入
      {
        path: 'evaluation/positive-entry',
        name: 'PositiveScoreEntry',
        component: () => import('@/views/evaluation/PositiveScoreEntry.vue'),
        meta: {
          title: '正向加分',
          icon: 'Plus',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // 学生正向加分查看
      {
        path: 'evaluation/positive-view',
        name: 'PositiveScoreStudentView',
        component: () => import('@/views/evaluation/PositiveScoreStudentView.vue'),
        meta: {
          title: '我的正能量',
          icon: 'Trophy',
          roles: ['STUDENT', 'PARENT'] as UserRole[],
        },
      },
      // 正能量排行榜
      {
        path: 'evaluation/positive-ranking',
        name: 'PositiveRanking',
        component: () => import('@/views/evaluation/PositiveRanking.vue'),
        meta: {
          title: '正能量排行榜',
          icon: 'Histogram',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER', 'STUDENT', 'PARENT'] as UserRole[],
        },
      },
      // 报告导出工作台
      {
        path: 'reports',
        name: 'ReportCenter',
        component: () => import('@/views/reports/Index.vue'),
        meta: {
          title: '报告工作台',
          icon: 'Document',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // 通知中心
      {
        path: 'notifications',
        name: 'NotificationCenter',
        component: () => import('@/views/notifications/Index.vue'),
        meta: {
          title: '通知中心',
          icon: 'Bell',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER', 'PARENT'] as UserRole[],
        },
      },
      // 成长档案 (P0双表: 时光轴+五维快照+全息画像)
      {
        path: 'growth',
        name: 'GrowthTimeline',
        component: () => import('@/views/growth/Index.vue'),
        meta: {
          title: '成长档案',
          icon: 'TrendCharts',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER', 'PARENT'] as UserRole[],
        },
      },
      // ── 成绩管理模块路由 ──
      // 成绩看板（宏观成绩概览）
      {
        path: 'grades/dashboard',
        name: 'GradesDashboard',
        component: () => import('@/views/grades/GradeDashboard.vue'),
        meta: {
          title: '成绩看板',
          icon: 'DataLine',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // 双模态全息雷达（学业+行为五维映射）
      {
        path: 'grades/radar',
        name: 'GradesRadar',
        component: () => import('@/views/grades/StudentRadarChart.vue'),
        meta: {
          title: '成绩雷达',
          icon: 'Aim',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // 全息档案容器（雷达+时间轴+AI处方）
      {
        path: 'grades/profile',
        name: 'GradesProfile',
        component: () => import('@/views/grades/HolisticProfileCard.vue'),
        meta: {
          title: '全息档案',
          icon: 'UserFilled',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // AI处方全景中心（批量浏览+筛选）
      {
        path: 'grades/prescriptions',
        name: 'PrescriptionCenter',
        component: () => import('@/views/grades/PrescriptionCenter.vue'),
        meta: {
          title: 'AI 处方中心',
          icon: 'MagicStick',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
          phases: ['junior', 'senior', 'integrated'],
        },
      },
      // ── 考勤管理 (德育域) ──
      {
        path: 'attendance',
        name: 'AttendanceCenter',
        component: () => import('@/views/attendance/Index.vue'),
        meta: {
          title: '考勤管理',
          icon: 'Calendar',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // ── 考勤全息历史大盘 ──
      {
        path: 'attendance/class-history',
        name: 'ClassHistoryDashboard',
        component: () => import('@/views/attendance/ClassHistoryDashboard.vue'),
        meta: {
          title: '考勤历史大盘',
          icon: 'TrendCharts',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // ── 流动红旗 (德育域) ──
      {
        path: 'red-flag',
        name: 'RedFlagCenter',
        component: () => import('@/views/red-flag/Index.vue'),
        meta: {
          title: '流动红旗',
          icon: 'Flag',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // ── 萌卡系统 (小学专属, 千人千面) ──
      {
        path: 'card-system',
        name: 'CardSystem',
        component: () => import('@/views/card-system/Index.vue'),
        meta: {
          title: '萌卡系统',
          icon: 'Box',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
          phases: ['primary', 'integrated'],
        },
      },
      // ── 高考学情大盘 (高中专属, 千人千面) ──
      {
        path: 'gaokao-dashboard',
        name: 'GaokaoDashboard',
        component: () => import('@/views/gaokao-dashboard/Index.vue'),
        meta: {
          title: '高考学情大盘',
          icon: 'TrophyBase',
          roles: ['MS_ADMIN', 'GRADE_LEADER'] as UserRole[],
          phases: ['senior', 'integrated'],
        },
      },
      // ── 数据并网适配层 (成绩导入+赋分管道, 初中/高中) ──
      {
        path: 'data-adapter',
        name: 'DataAdapter',
        component: () => import('@/views/data-adapter/Index.vue'),
        meta: {
          title: '数据并网',
          icon: 'Connection',
          roles: ['MS_ADMIN', 'GRADE_LEADER'] as UserRole[],
          phases: ['junior', 'senior', 'integrated'],
        },
      },
      // ── 心理筛查与干预 (暖色调心理板块) ──
      {
        path: 'psych-screening',
        name: 'PsychScreening',
        component: () => import('@/views/psych-screening/Index.vue'),
        meta: {
          title: '心理筛查',
          icon: 'Sunny',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      {
        path: 'psych-screening/fill',
        name: 'PsychSurveyFill',
        component: () => import('@/views/psych-screening/SurveyFill.vue'),
        meta: {
          title: '量表填写',
          icon: 'EditPen',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      {
        path: 'psych-screening/result',
        name: 'PsychResultView',
        component: () => import('@/views/psych-screening/ResultView.vue'),
        meta: {
          title: '筛查结果',
          icon: 'DataAnalysis',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      {
        path: 'psych-screening/intervention',
        name: 'PsychIntervention',
        component: () => import('@/views/psych-screening/Intervention.vue'),
        meta: {
          title: '干预管理',
          icon: 'FirstAidKit',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      {
        path: 'psych-screening/portrait',
        name: 'PsychPortrait',
        component: () => import('@/views/psych-screening/PortraitView.vue'),
        meta: {
          title: '心理画像与交叉分析',
          icon: 'PieChart',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      // ── 心理咨询工作台 (Phase2: 咨询预约+加密写实) ──
      {
        path: 'counselor-console',
        name: 'CounselorConsole',
        component: () => import('@/views/counselor-console/Index.vue'),
        meta: {
          title: '心理咨询工作台',
          icon: 'ChatDotRound',
          roles: ['MS_ADMIN', 'GRADE_LEADER'] as UserRole[],
        },
      },
      // ── NexusBoard 双轨预警决策看板 (学业×心理交叉风控) ──
      {
        path: 'nexus-board',
        name: 'NexusBoard',
        component: () => import('@/views/nexus-board/Index.vue'),
        meta: {
          title: '双轨预警决策看板',
          icon: 'DataLine',
          roles: ['MS_ADMIN', 'GRADE_LEADER'] as UserRole[],
        },
      },
      // ── 数据铁三角: 学籍+班级管理 ──
      {
        path: 'student-registry',
        name: 'StudentRegistry',
        component: () => import('@/views/student-registry/Index.vue'),
        meta: {
          title: '学籍管理',
          icon: 'User',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      {
        path: 'student-registry/detail',
        name: 'StudentDetail',
        component: () => import('@/views/student-registry/Detail.vue'),
        meta: {
          title: '学籍详情',
          icon: 'UserFilled',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      {
        path: 'class-mgmt',
        name: 'ClassManagement',
        component: () => import('@/views/class-mgmt/Index.vue'),
        meta: {
          title: '班级管理',
          icon: 'Grid',
          roles: ['MS_ADMIN', 'GRADE_LEADER'] as UserRole[],
        },
      },
      // ── 教师管理 ──
      {
        path: 'teacher-mgmt',
        name: 'TeacherManagement',
        component: () => import('@/views/teacher-mgmt/Index.vue'),
        meta: {
          title: '教师管理',
          icon: 'Avatar',
          roles: ['MS_ADMIN', 'GRADE_LEADER'] as UserRole[],
        },
      },
      {
        path: 'teacher-mgmt/detail/:id',
        name: 'TeacherDetail',
        component: () => import('@/views/teacher-mgmt/Detail.vue'),
        meta: { title: '教师详情', hidden: true },
      },
      {
        path: 'teacher-mgmt/workload/:id',
        name: 'TeacherWorkload',
        component: () => import('@/views/teacher-mgmt/Workload.vue'),
        meta: { title: '工作量统计', hidden: true },
      },
      // ── 课程表管理 ──
      {
        path: 'timetable',
        name: 'Timetable',
        component: () => import('@/views/timetable/Index.vue'),
        meta: {
          title: '课程表管理',
          icon: 'Calendar',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
        },
      },
      {
        path: 'timetable/week/:classId',
        name: 'TimetableWeekView',
        component: () => import('@/views/timetable/WeekView.vue'),
        meta: { title: '班级周课表', hidden: true },
      },
      {
        path: 'timetable/week/teacher/:teacherId',
        name: 'TeacherWeekView',
        component: () => import('@/views/timetable/WeekView.vue'),
        meta: { title: '教师周课表', hidden: true },
      },
      {
        path: 'timetable/conflicts',
        name: 'TimetableConflicts',
        component: () => import('@/views/timetable/Conflicts.vue'),
        meta: { title: '排课冲突', hidden: true },
      },
      // ── 小学虚拟萌卡激励系统 ──
      {
        path: 'habit-cards',
        name: 'HabitCards',
        component: () => import('@/views/habit-cards/Index.vue'),
        meta: {
          title: '萌卡荣誉生态',
          icon: 'Medal',
          roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'] as UserRole[],
          phases: ['primary', 'integrated'],
        },
      },
      // ── 家长门户路由 ──
      // 家长仪表盘（家长首页）
      {
        path: 'parent',
        name: 'ParentPortal',
        component: () => import('@/views/parent/ParentPortal.vue'),
        meta: {
          title: '家长门户',
          icon: 'HomeFilled',
          roles: ['PARENT'] as UserRole[],
        },
      },
      // 家校反馈
      {
        path: 'parent/feedback',
        name: 'ParentFeedback',
        component: () => import('@/views/parent/ParentFeedback.vue'),
        meta: {
          title: '家校反馈',
          icon: 'ChatLineSquare',
          roles: ['PARENT'] as UserRole[],
        },
      },
      // 在线申诉
      {
        path: 'parent/appeal',
        name: 'ParentAppeal',
        component: () => import('@/views/parent/ParentAppeal.vue'),
        meta: {
          title: '在线申诉',
          icon: 'WarningFilled',
          roles: ['PARENT'] as UserRole[],
        },
      },
      // 金色盲盒 H5 落地页 (Task #1400)
      {
        path: 'parent/blindbox',
        name: 'ParentBlindbox',
        component: () => import('@/views/parent/ParentBlindbox.vue'),
        meta: {
          title: '金色盲盒',
          icon: 'Present',
          roles: ['PARENT'] as UserRole[],
          phases: ['primary', 'integrated'],
        },
      },
      // 心理咨询预约中心 (AppointmentPicker, Task #1415)
      {
        path: 'parent/appointment',
        name: 'AppointmentPicker',
        component: () => import('@/views/parent/AppointmentPicker.vue'),
        meta: {
          title: '心理咨询预约',
          icon: 'Clock',
          roles: ['PARENT'] as UserRole[],
        },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/NotFound.vue'),
    meta: { title: '404', public: true },
  },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

/**
 * Global Navigation Guard — RBAC + Auth Check
 */
router.beforeEach((to, _from, next) => {
  const userStore = useUserStore()
  const isPublic = to.meta.public === true

  // Set page title
  const title = (to.meta.title as string) || 'Wings 3.0'
  document.title = `${title} - Wings 3.0`

  // Public route (login, 404)
  if (isPublic) {
    // Already logged in and going to /login → redirect to home
    if (to.name === 'Login' && userStore.isLoggedIn) {
      next({ path: '/' })
      return
    }
    next()
    return
  }

  // Not logged in → redirect to login
  if (!userStore.isLoggedIn) {
    next({ path: '/login', query: { redirect: to.fullPath } })
    return
  }

  // PARENT 角色默认跳转到家长门户（而非 /dashboard）
  if (to.path === '/' || to.path === '/dashboard') {
    if (userStore.currentRole === 'PARENT') {
      next({ path: '/parent' })
      return
    }
  }

  // Role-based access control
  const requiredRoles = to.meta.roles as UserRole[] | undefined
  if (requiredRoles && requiredRoles.length > 0) {
    const userRole = userStore.currentRole
    if (!userRole || !requiredRoles.includes(userRole)) {
      // 角色不匹配时，PARENT → /parent，其他 → /
      next({ path: userStore.currentRole === 'PARENT' ? '/parent' : '/' })
      return
    }
  }

  // Phase-based access control (学段隔离闸)
  // MS_ADMIN 超管跳过学段检查（可管理所有学段）
  const requiredPhases = to.meta.phases as string[] | undefined
  if (requiredPhases && requiredPhases.length > 0 && userStore.currentRole !== 'MS_ADMIN') {
    const currentPhase = userStore.currentPhase
    if (currentPhase && !requiredPhases.includes(currentPhase)) {
      // 学段不匹配 → 重定向到首页（菜单里也看不到，直接URL攻击拦截）
      next({ path: '/' })
      return
    }
  }

  next()
})

export default router
