<template>
  <!-- 🔪 Fix #3: 启动会话验证 — 防止过期token闪烁旧侧边栏 -->
  <div v-if="isSessionLoading" class="session-loading">
    <el-skeleton :rows="8" animated />
  </div>

  <el-container v-else class="main-layout">
    <!-- Sidebar: dark theme, grouped + role-filtered menu -->
    <el-aside :width="isCollapsed ? '64px' : '240px'" class="sidebar">
      <div class="logo-section">
        <img src="/favicon.svg" alt="logo" class="logo-icon" v-if="!isCollapsed" />
        <h1 v-if="!isCollapsed" class="logo-text">Wings 3.0</h1>
        <img src="/favicon.svg" alt="logo" class="logo-icon-collapsed" v-else />
      </div>

      <el-menu
        v-if="visibleGroups.length > 0"
        :default-active="activeMenu"
        :collapse="isCollapsed"
        :collapse-transition="false"
        :unique-opened="true"
        router
        class="sidebar-menu"
        background-color="#1f2c3f"
        text-color="#bfcbd9"
        active-text-color="#ffffff"
      >
        <el-sub-menu
          v-for="group in visibleGroups"
          :key="group.title"
          :index="group.title"
        >
          <template #title>
            <el-icon><component :is="group.icon" /></el-icon>
            <span>{{ group.title }}</span>
          </template>

          <el-menu-item
            v-for="item in group.items"
            :key="item.index"
            :index="item.index"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <template #title>
              <span class="menu-title-text">{{ item.title }}</span>
              <el-badge
                v-if="item.index === '/approval-center' && pendingCount > 0"
                :value="pendingCount"
                :max="99"
                class="approval-badge"
              />
            </template>
          </el-menu-item>
        </el-sub-menu>
      </el-menu>

      <!-- Empty state for PARENT (no accessible menu items) -->
      <div v-else class="empty-menu">
        <el-icon class="empty-icon"><WarningFilled /></el-icon>
        <p v-if="!isCollapsed" class="empty-text">
          当前角色（{{ userStore.currentRoleLabel }}）暂无可访问的功能模块
        </p>
        <p v-if="!isCollapsed" class="empty-hint">
          家长请使用微信小程序或<a href="/" class="empty-link">家长门户</a>
        </p>
      </div>

      <!-- 版本指纹：确认线上实际运行的是哪一版代码（与 GitHub 最新 commit 比对） -->
      <div v-if="!isCollapsed" class="version-fingerprint" :title="versionTip">
        WINGS 3.0 · {{ versionEnv }} ·
        <code>{{ shortCommit }}</code>
      </div>
    </el-aside>

    <!-- Main area -->
    <el-container class="main-container">
      <!-- Header: school name highlight + user dropdown -->
      <el-header class="header" height="60px">
        <div class="header-left">
          <el-icon class="collapse-btn" @click="toggleSidebar">
            <Fold v-if="!isCollapsed" />
            <Expand v-else />
          </el-icon>
          <div class="school-highlight">
            <el-icon><School /></el-icon>
            <span class="school-name">{{ tenantStore.currentSchoolName }}</span>
            <el-tag
              size="small"
              :type="(phaseTagTypes[userStore.currentPhase] || 'info') as any"
              effect="dark"
              class="school-tag"
            >
              {{ phaseLabels[userStore.currentPhase] || '未知学段' }}
            </el-tag>
          </div>
        </div>

        <div class="header-right">
          <!-- 通知铃铛 — 未读 Badge + Popover 下拉 -->
          <NotificationBell @view-all="handleViewAllNotifications" />

          <el-dropdown trigger="click" @command="handleCommand">
            <div class="user-info">
              <el-avatar :size="32" class="user-avatar">
                {{ userStore.userInfo?.real_name?.charAt(0) || 'U' }}
              </el-avatar>
              <div class="user-detail">
                <span class="user-name">{{ userStore.userInfo?.real_name || '未登录' }}</span>
                <el-tag size="small" :type="roleTagType" class="role-tag">
                  {{ userStore.currentRoleLabel }}
                </el-tag>
              </div>
              <el-icon class="dropdown-arrow"><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><User /></el-icon> 个人信息
                </el-dropdown-item>
                <el-dropdown-item command="settings">
                  <el-icon><Setting /></el-icon> 系统设置
                </el-dropdown-item>
                <el-dropdown-item divided command="logout">
                  <el-icon><SwitchButton /></el-icon> 退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- Content area with transition animation -->
      <el-main class="content-area">
        <router-view v-slot="{ Component }">
          <transition name="fade-transform" mode="out-in">
            <keep-alive>
              <component :is="Component" />
            </keep-alive>
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox, ElMessage } from 'element-plus'
import { useUserStore } from '@/store/user'
import { useTenantStore } from '@/store/tenant'
import { getPendingCount } from '@/api/approval'
import { getCurrentUser } from '@/api/auth'
import request from '@/api/request'
import NotificationBell from '@/views/notifications/NotificationBell.vue'
import type { UserRole } from '@/types'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const tenantStore = useTenantStore()

const isCollapsed = ref(false)
const pendingCount = ref(0)

// 🔪 Fix #3: 启动会话验证状态 — 防止过期token闪烁旧侧边栏
const isSessionLoading = ref(true)

const activeMenu = computed(() => route.path)

const roleTagType = computed(() => {
  switch (userStore.currentRole) {
    case 'MS_ADMIN':
      return 'danger'
    case 'GRADE_LEADER':
      return 'warning'
    case 'CLASS_TEACHER':
      return 'success'
    case 'PARENT':
      return 'info'
    default:
      return 'info'
  }
})

interface MenuItem {
  index: string
  title: string
  icon: string
  roles: UserRole[]
  /** 🎯 千人千面: 该菜单项对哪些学段可见，缺省=全学段 */
  phases?: string[]
  /** 🎯 千人千面: 依赖的插件开关名，对应 plugin_config 的 key */
  plugin?: string
}

interface MenuGroup {
  title: string
  icon: string
  items: MenuItem[]
}

// ── 学段标签映射 ──
const phaseLabels: Record<string, string> = {
  primary: '小学',
  junior: '初中',
  senior: '高中',
  integrated: '完中',
}

// ── 学段标签颜色 ──
const phaseTagTypes: Record<string, string> = {
  primary: 'success',
  junior: 'warning',
  senior: 'danger',
  integrated: 'primary',
}

// ─────────────────────────────────────────────────────────────
// 菜单分组定义 — 五域分类（Phase A 重组）
// 与 router/index.ts 的 meta.roles 保持 1:1 对齐
// 分组策略：公共底座 → 德育管理 → 教导管理 → 教研工具 → 心理关怀 → 家长门户
// 教师角色看到前5组，家长只看到"家长门户"
// ─────────────────────────────────────────────────────────────
const allMenuGroups: MenuGroup[] = [
  // ── 1. 公共底座：跨业务域的基础设施 ──
  {
    title: '公共底座',
    icon: 'Platform',
    items: [
      {
        index: '/dashboard',
        title: '指挥舱看板',
        icon: 'DataLine',
        roles: ['MS_ADMIN', 'GRADE_LEADER'],
      },
      {
        index: '/notifications',
        title: '通知中心',
        icon: 'Bell',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/growth',
        title: '成长档案',
        icon: 'TrendCharts',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/grades/profile',
        title: '全息档案',
        icon: 'UserFilled',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      // 数据铁三角: 学籍 + 班级管理
      {
        index: '/student-registry',
        title: '学籍管理',
        icon: 'User',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/class-mgmt',
        title: '班级管理',
        icon: 'Grid',
        roles: ['MS_ADMIN', 'GRADE_LEADER'],
      },
      {
        index: '/teacher-mgmt',
        title: '教师管理',
        icon: 'Avatar',
        roles: ['MS_ADMIN', 'GRADE_LEADER'],
      },
    ],
  },
  // ── 2. 德育管理中心：违纪→处分→评价→风险→处方闭环 ──
  {
    title: '德育管理中心',
    icon: 'Shield',
    items: [
      {
        index: '/rdi-radar',
        title: 'RDI 风险雷达',
        icon: 'Monitor',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
        phases: ['junior', 'senior', 'integrated'],
        plugin: 'enable_rdi',
      },
      {
        index: '/rdi-dashboard',
        title: 'RDI 风险看板',
        icon: 'Odometer',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
        phases: ['junior', 'senior', 'integrated'],
        plugin: 'enable_rdi',
      },
      {
        index: '/approval-center',
        title: '审批工作台',
        icon: 'Checked',
        roles: ['MS_ADMIN', 'GRADE_LEADER'],
      },
      {
        index: '/behavior',
        title: '德育与处分中心',
        icon: 'Warning',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/discipline',
        title: '惩戒流转中心',
        icon: 'Stamp',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/ai-prescription',
        title: 'AI 德育处方',
        icon: 'MagicStick',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
        phases: ['junior', 'senior', 'integrated'],
      },
      {
        index: '/evaluation',
        title: '素质评价',
        icon: 'TrendCharts',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/evaluation/positive-entry',
        title: '正向加分',
        icon: 'Plus',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/evaluation/positive-ranking',
        title: '正能量排行榜',
        icon: 'Histogram',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/reports',
        title: '报告工作台',
        icon: 'Document',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/attendance',
        title: '考勤管理',
        icon: 'Calendar',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/attendance/class-history',
        title: '考勤历史大盘',
        icon: 'TrendCharts',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/red-flag',
        title: '流动红旗',
        icon: 'Flag',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      // 🎯 千人千面: 小学萌卡系统入口
      {
        index: '/card-system',
        title: '萌卡系统',
        icon: 'Box',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
        phases: ['primary'],
        plugin: 'enable_card',
      },
    ],
  },
  // ── 3. 教导管理中心：成绩→分析→报告闭环 ──
  {
    title: '教导管理中心',
    icon: 'School',
    items: [
      {
        index: '/grades/dashboard',
        title: '成绩看板',
        icon: 'DataLine',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
        phases: ['junior', 'senior', 'integrated'],
      },
      {
        index: '/grades/radar',
        title: '成绩雷达',
        icon: 'Aim',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
        phases: ['junior', 'senior', 'integrated'],
      },
      {
        index: '/grades/prescriptions',
        title: 'AI 处方中心',
        icon: 'MagicStick',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
        phases: ['junior', 'senior', 'integrated'],
      },
      // 🎯 千人千面: 高中高考学情大盘入口
      {
        index: '/gaokao-dashboard',
        title: '高考学情大盘',
        icon: 'TrophyBase',
        roles: ['MS_ADMIN', 'GRADE_LEADER'],
        phases: ['senior', 'integrated'],
      },
      {
        index: '/data-adapter',
        title: '数据并网',
        icon: 'Connection',
        roles: ['MS_ADMIN', 'GRADE_LEADER'],
        phases: ['junior', 'senior', 'integrated'],
      },
      {
        index: '/timetable',
        title: '课程表管理',
        icon: 'Calendar',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/habit-cards',
        title: '萌卡荣誉生态',
        icon: 'Medal',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
        phases: ['primary', 'integrated'],
      },
      {
        index: '/homework',
        title: '作业管理',
        icon: 'EditPen',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
        phases: ['junior', 'senior', 'integrated'],
      },
      {
        index: '/error-funnel',
        title: '错题断层',
        icon: 'Filter',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
        phases: ['junior', 'senior', 'integrated'],
      },
    ],
  },
  // ── 4. 教研工具：学科教学辅助 ──
  {
    title: '教研工具',
    icon: 'Tools',
    items: [
      {
        index: '/research',
        title: '教研协同',
        icon: 'Coordinate',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/teach-math/coach',
        title: '审题助手',
        icon: 'Reading',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/teach-math/report',
        title: '审题诊断',
        icon: 'DataAnalysis',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
    ],
  },
  // ── 5. 心理关怀：筛查→干预→画像→危机闭环 ──
  {
    title: '心理关怀',
    icon: 'Sunrise',
    items: [
      {
        index: '/psych-screening',
        title: '心理筛查',
        icon: 'Sunny',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/psych-screening/intervention',
        title: '干预管理',
        icon: 'FirstAidKit',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/psych-screening/portrait',
        title: '画像与交叉分析',
        icon: 'PieChart',
        roles: ['MS_ADMIN', 'GRADE_LEADER', 'CLASS_TEACHER'],
      },
      {
        index: '/counselor-console',
        title: '心理咨询工作台',
        icon: 'ChatDotRound',
        roles: ['MS_ADMIN', 'GRADE_LEADER'],
      },
      {
        index: '/nexus-board',
        title: '双轨预警决策看板',
        icon: 'DataLine',
        roles: ['MS_ADMIN', 'GRADE_LEADER'],
      },
    ],
  },
  // ── 6. 家长门户：家长专属只读视图 ──
  {
    title: '家长门户',
    icon: 'HomeFilled',
    items: [
      {
        index: '/parent',
        title: '家长首页',
        icon: 'HomeFilled',
        roles: ['PARENT'],
      },
      {
        index: '/parent/feedback',
        title: '家校反馈',
        icon: 'ChatLineSquare',
        roles: ['PARENT'],
      },
      {
        index: '/parent/appeal',
        title: '在线申诉',
        icon: 'WarningFilled',
        roles: ['PARENT'],
      },
      {
        index: '/notifications',
        title: '通知中心',
        icon: 'Bell',
        roles: ['PARENT'],
      },
      {
        index: '/growth',
        title: '成长时间轴',
        icon: 'TrendCharts',
        roles: ['PARENT'],
      },
      {
        index: '/evaluation/positive-view',
        title: '我的正能量',
        icon: 'Trophy',
        roles: ['PARENT'],
      },
      {
        index: '/evaluation/positive-ranking',
        title: '正能量排行榜',
        icon: 'Histogram',
        roles: ['PARENT'],
      },
      {
        index: '/parent/appointment',
        title: '心理咨询预约',
        icon: 'Clock',
        roles: ['PARENT'],
      },
    ],
  },
]

// ─────────────────────────────────────────────────────────────
// 🎯 千人千面: 按角色 + 学段 + 插件配置 三重过滤菜单
//
// 1. 角色过滤: MS_ADMIN/GRADE_LEADER/CLASS_TEACHER/PARENT
// 2. 学段过滤: primary/junior/senior/integrated
//    - MS_ADMIN 超管跳过学段过滤（可看到所有学段的入口）
// 3. 插件过滤: plugin_config 中对应 key 必须为 true
// ─────────────────────────────────────────────────────────────
const visibleGroups = computed<MenuGroup[]>(() => {
  const role = userStore.currentRole
  if (!role) return []
  const currentPhase = userStore.currentPhase
  const pluginConfig = userStore.pluginConfig
  const isSuperAdmin = role === 'MS_ADMIN'

  return allMenuGroups
    .map((g) => ({
      ...g,
      items: g.items.filter((item) => {
        // 1. 角色过滤
        if (!item.roles.includes(role)) return false

        // 2. 学段过滤（超管跳过）
        if (!isSuperAdmin && item.phases && !item.phases.includes(currentPhase)) {
          return false
        }

        // 3. 插件开关过滤
        if (item.plugin) {
          const enabled = pluginConfig?.[item.plugin] === true
          if (!enabled) return false
        }

        return true
      }),
    }))
    .filter((g) => g.items.length > 0)
})

function toggleSidebar() {
  isCollapsed.value = !isCollapsed.value
}

async function fetchPendingCount() {
  try {
    const res: any = await getPendingCount()
    pendingCount.value = res?.count ?? 0
  } catch {
    // Silent fail — badge is non-critical
  }
}

function handleViewAllNotifications() {
  router.push('/notifications')
}

function handleCommand(command: string) {
  switch (command) {
    case 'profile':
      ElMessage.info('个人信息页面开发中...')
      break
    case 'settings':
      ElMessage.info('系统设置页面开发中...')
      break
    case 'logout':
      ElMessageBox.confirm('确定要退出登录吗？', '提示', {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning',
      }).then(() => {
        userStore.clearAuth()
        router.push('/login')
      }).catch(() => {})
      break
  }
}

/**
 * 🔪 Fix #3: 启动会话验证 — 刷新页面时调用 /auth/me 刷新 userInfo
 *
 * 解决 localStorage 有过期token → 侧边栏短暂闪烁旧数据 → 401踢出 的UX问题
 * 流程: 骨架屏 → /auth/me → 成功(刷新userInfo+渲染) / 失败(401拦截器自动跳login)
 */
async function validateSession() {
  if (!userStore.isLoggedIn) {
    // 无token → 路由守卫已拦截到/login，不会渲染MainLayout
    isSessionLoading.value = false
    return
  }

  try {
    const freshUserInfo = await getCurrentUser()
    // setUserInfo 归一化: display_name→real_name, role→大写UserRole, school_phase→plugin_config
    userStore.setUserInfo(freshUserInfo)
    // 同步租户信息（含学段）
    if (freshUserInfo.school_id && freshUserInfo.school_name) {
      tenantStore.setSchool(
        freshUserInfo.school_id,
        freshUserInfo.school_name,
        freshUserInfo.school_phase,
      )
    }
  } catch {
    // 401拦截器已自动 clearAuth + 硬跳转login；此处不重复处理
  } finally {
    isSessionLoading.value = false
  }
}

// ── 版本指纹（可确认性）──
// 目的：一眼确认线上跑的是哪一版代码。GitHub 最新 commit == 此处 commit 即已部署。
const versionEnv = ref('unknown')
const versionCommit = ref('unknown')
const versionBuildTime = ref('')

const shortCommit = computed(() =>
  versionCommit.value === 'unknown' ? 'unknown' : versionCommit.value.slice(0, 7),
)
const versionTip = computed(
  () => `commit: ${versionCommit.value}\nbuild: ${versionBuildTime.value || 'unknown'}`,
)

async function loadVersionFingerprint() {
  try {
    const { data } = await request.get('/version')
    versionEnv.value = data?.environment || 'unknown'
    versionCommit.value = data?.commit || 'unknown'
    versionBuildTime.value = data?.build_time || ''
  } catch {
    // 版本指纹属可观测性增强，失败不得影响主流程
  }
}

onMounted(() => {
  validateSession()
  // Poll pending approval count every 60s
  fetchPendingCount()
  setInterval(fetchPendingCount, 60000)
  loadVersionFingerprint()
})
</script>

<style scoped>
.main-layout {
  height: 100vh;
  overflow: hidden;
}

.sidebar {
  background-color: #1f2c3f;
  transition: width 0.3s ease;
  overflow: hidden;
}

.logo-section {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: linear-gradient(135deg, #1e6091 0%, #184d74 100%);
}

.logo-icon {
  width: 28px;
  height: 28px;
}

.logo-icon-collapsed {
  width: 28px;
  height: 28px;
}

.logo-text {
  color: #fff;
  font-size: 18px;
  font-weight: 600;
  white-space: nowrap;
}

.version-fingerprint {
  margin-top: auto;
  padding: 10px 16px;
  font-size: 11px;
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.45);
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  user-select: all;
}

.version-fingerprint code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: rgba(255, 255, 255, 0.65);
}

.sidebar-menu {
  border-right: none;
}

.sidebar-menu:not(.el-menu--collapse) {
  width: 240px;
}

/* ── 品牌化菜单项 ── */
.sidebar-menu :deep(.el-menu-item),
.sidebar-menu :deep(.el-sub-menu__title) {
  height: 44px;
  line-height: 44px;
  border-radius: 8px;
  margin: 4px 10px;
  color: #bfcbd9;
  transition: all 0.2s ease;
}

.sidebar-menu :deep(.el-sub-menu__title) {
  font-weight: 600;
}

/* hover：浅白底 + 白字 */
.sidebar-menu :deep(.el-menu-item:hover),
.sidebar-menu :deep(.el-sub-menu__title:hover) {
  background: rgba(255, 255, 255, 0.1) !important;
  color: #fff !important;
}

/* 激活态：主色填充 + 白字 + 左侧白条 */
.sidebar-menu :deep(.el-menu-item) {
  position: relative;
}
.sidebar-menu :deep(.el-menu-item.is-active) {
  background: #1e6091 !important;
  color: #fff !important;
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(30, 96, 145, 0.45);
}
.sidebar-menu :deep(.el-menu-item.is-active)::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 4px;
  height: 22px;
  background: #fff;
  border-radius: 0 4px 4px 0;
}

.menu-title-text {
  display: inline-block;
}

.approval-badge {
  margin-left: 8px;
}

/* 空状态 — PARENT 角色无可用菜单 */
.empty-menu {
  padding: 40px 16px;
  text-align: center;
  color: #909399;
}

.empty-icon {
  font-size: 40px;
  color: #5a5e66;
  margin-bottom: 12px;
}

.empty-text {
  font-size: 13px;
  line-height: 1.6;
  margin: 0 0 8px;
  color: #bfcbd9;
}

.empty-hint {
  font-size: 12px;
  line-height: 1.6;
  margin: 0;
  color: #7a8088;
}

.empty-link {
  color: #1e6091;
  text-decoration: none;
}

.empty-link:hover {
  text-decoration: underline;
}

.main-container {
  height: 100vh;
  overflow: hidden;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
  z-index: 10;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 20px;
}

.collapse-btn {
  font-size: 20px;
  cursor: pointer;
  color: #5a5e66;
  transition: color 0.2s;
}

.collapse-btn:hover {
  color: #1e6091;
}

.school-highlight {
  display: flex;
  align-items: center;
  gap: 8px;
}

.school-name {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.school-tag {
  margin-left: 4px;
}

.header-right {
  display: flex;
  align-items: center;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  padding: 0 12px;
  height: 60px;
  transition: background 0.2s;
  border-radius: 4px;
}

.user-info:hover {
  background: #f5f7fa;
}

.user-avatar {
  background-color: #1e6091;
  color: #fff;
  font-weight: 600;
}

.user-detail {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.user-name {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
}

.role-tag {
  align-self: flex-start;
}

.dropdown-arrow {
  color: #909399;
  font-size: 12px;
}

.content-area {
  background: #f0f2f5;
  padding: 20px;
  overflow-y: auto;
  height: calc(100vh - 60px);
}

/* 🔪 Fix #3: 启动会话验证骨架屏 — 防止旧数据闪烁 */
.session-loading {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f0f2f5;
  padding: 40px;
}
</style>
