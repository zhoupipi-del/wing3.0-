<template>
  <div class="class-history-dashboard">
    <!-- ── 顶部控制栏 ── -->
    <div class="control-bar">
      <div class="control-left">
        <!-- 班级选择器: 班主任锁定本班, 年级组长/超管可选所有班级 -->
        <el-select
          v-model="selectedClassId"
          :disabled="isClassTeacher"
          placeholder="选择班级"
          class="class-selector"
          @change="handleClassChange"
        >
          <el-option
            v-for="cls in classOptions"
            :key="cls.id"
            :label="cls.name"
            :value="cls.id"
          />
        </el-select>

        <!-- 日期范围选择器 -->
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="→"
          start-placeholder="起始日期"
          end-placeholder="截止日期"
          :shortcuts="dateShortcuts"
          :clearable="false"
          class="date-range-picker"
          @change="handleDateChange"
        />
      </div>

      <div class="control-right">
        <!-- 扣分公式提示 -->
        <el-tooltip placement="bottom-end">
          <template #content>
            <div class="formula-tooltip">
              考勤扣分公式: 100 − 缺勤×15 − 迟到×5 − 早退×5<br />
              缺勤(CRITICAL)权重最高，请假(INFO)不扣分<br />
              冲正机制: 请假审批通过后自动清洗 absent→leave
            </div>
          </template>
          <el-tag effect="dark" type="warning" class="formula-tag">
            公式: 100 − absent×15 − late×5
          </el-tag>
        </el-tooltip>
      </div>
    </div>

    <!-- ── 数据摘要卡片 ── -->
    <div class="summary-cards" v-if="historyData.length > 0">
      <div class="summary-card">
        <div class="card-label">数据覆盖天数</div>
        <div class="card-value">{{ historyData.length }}天</div>
      </div>
      <div class="summary-card">
        <div class="card-label">平均出勤率</div>
        <div class="card-value rate-value">{{ avgRate }}%</div>
      </div>
      <div class="summary-card">
        <div class="card-label">累计缺勤(CRITICAL)</div>
        <div class="card-value critical-value">{{ totalAbsent }}人次</div>
      </div>
      <div class="summary-card">
        <div class="card-label">累计迟到+早退</div>
        <div class="card-value warning-value">{{ totalLate + totalEarly }}人次</div>
      </div>
      <div class="summary-card">
        <div class="card-label">累计请假(INFO)</div>
        <div class="card-value info-value">{{ totalLeave }}人次</div>
      </div>
    </div>

    <!-- ── 空状态 ── -->
    <div class="empty-state" v-if="!isLoading && historyData.length === 0">
      <el-empty description="请选择班级和日期范围加载考勤历史数据" />
    </div>

    <!-- ── 加载状态 ── -->
    <div class="loading-state" v-if="isLoading">
      <el-skeleton :rows="8" animated />
    </div>

    <!-- ── ECharts 双轴复合趋势图 ── -->
    <div class="chart-container" v-show="!isLoading && historyData.length > 0">
      <div ref="trendChartRef" class="trend-chart"></div>
    </div>

    <!-- ── 异常日期明细表 ── -->
    <div class="detail-section" v-if="!isLoading && abnormalDays.length > 0">
      <div class="section-header">
        <el-icon><Warning /></el-icon>
        <span>异常日期明细 (出勤率 &lt; 90% 或 缺勤 ≥ 3人次)</span>
      </div>
      <el-table :data="abnormalDays" stripe class="abnormal-table" size="small" max-height="300">
        <el-table-column prop="date" label="日期" width="120" />
        <el-table-column prop="attendance_rate" label="出勤率" width="100">
          <template #default="{ row }">
            <el-tag :type="row.attendance_rate >= 95 ? 'success' : row.attendance_rate >= 90 ? 'warning' : 'danger'" effect="dark" size="small">
              {{ row.attendance_rate.toFixed(1) }}%
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="absent" label="缺勤" width="80">
          <template #default="{ row }">
            <span :class="{ 'text-critical': row.absent > 0 }">{{ row.absent }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="late" label="迟到" width="80" />
        <el-table-column prop="early" label="早退" width="80" />
        <el-table-column prop="leave" label="请假" width="80" />
        <el-table-column label="扣分" width="90">
          <template #default="{ row }">
            <span class="deduction-value">−{{ calcDeduction(row) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as echarts from 'echarts/core'
import '@/utils/echarts'
import { useUserStore } from '@/store/user'
import { getClassAttendanceHistory, type ClassHistoryMetric } from '@/api/attendance'
import { getClasses, getGrades } from '@/api/classes'

// ── 暗色主题色系 ──
const BG_PRIMARY = '#0d1117'
const BG_SECONDARY = '#161b22'
const BG_TERTIARY = '#30363d'
const TEXT_PRIMARY = '#e6edf3'
const TEXT_SECONDARY = '#8b949e'
const BORDER_COLOR = '#30363d'
const ACCENT_BLUE = '#58a6ff'
const ACCENT_GREEN = '#3fb950'
const ACCENT_YELLOW = '#d29922'
const ACCENT_RED = '#f85149'
const ACCENT_PURPLE = '#bc8cff'

const userStore = useUserStore()

// ── 状态 ──
const selectedClassId = ref<number>(0)
const dateRange = ref<[Date, Date]>(getDefaultDateRange())
const historyData = ref<ClassHistoryMetric[]>([])
const isLoading = ref(false)
const classOptions = ref<Array<{ id: number; name: string }>>([])
const gradeOptions = ref<Array<{ id: number; name: string }>>([])

// ── 图表实例 ──
const trendChartRef = ref<HTMLDivElement>()
let trendChart: ReturnType<typeof echarts.init> | null = null

// ── 角色判断 ──
const isClassTeacher = computed(() => userStore.currentRole === 'CLASS_TEACHER')
const isGradeLeader = computed(() => userStore.currentRole === 'GRADE_LEADER')
const isAdmin = computed(() => userStore.currentRole === 'MS_ADMIN')

// ── 统计摘要 ──
const avgRate = computed(() => {
  if (historyData.value.length === 0) return 0
  return historyData.value.reduce((sum, d) => sum + d.attendance_rate, 0) / historyData.value.length
})

const totalAbsent = computed(() => historyData.value.reduce((sum, d) => sum + d.absent, 0))
const totalLate = computed(() => historyData.value.reduce((sum, d) => sum + d.late, 0))
const totalEarly = computed(() => historyData.value.reduce((sum, d) => sum + d.early, 0))
const totalLeave = computed(() => historyData.value.reduce((sum, d) => sum + d.leave, 0))

// ── 异常日期 (出勤率<90% 或 缺勤≥3人次) ──
const abnormalDays = computed(() => {
  return historyData.value.filter(d => d.attendance_rate < 90 || d.absent >= 3)
})

// ── 扣分计算 ──
function calcDeduction(row: any): number {
  return (row.absent ?? 0) * 15 + (row.late ?? 0) * 5 + (row.early ?? 0) * 5
}

// ── 日期快捷选项 ──
const dateShortcuts = [
  { text: '近7天', value: () => { const end = new Date(); const start = new Date(); start.setTime(start.getTime() - 7 * 86400000); return [start, end] } },
  { text: '近30天', value: () => { const end = new Date(); const start = new Date(); start.setTime(start.getTime() - 30 * 86400000); return [start, end] } },
  { text: '近90天', value: () => { const end = new Date(); const start = new Date(); start.setTime(start.getTime() - 90 * 86400000); return [start, end] } },
  { text: '本学期', value: () => { const end = new Date(); const start = new Date('2026-02-16'); return [start, end] } },
]

function getDefaultDateRange(): [Date, Date] {
  const end = new Date()
  const start = new Date()
  start.setTime(start.getTime() - 30 * 86400000)
  return [start, end]
}

// ── 初始化班级选项 ──
async function initClassOptions() {
  try {
    if (isClassTeacher.value) {
      // 班主任: 锁定本班
      const cid = userStore.userInfo?.class_id
      const cname = userStore.userInfo?.class_name
      if (cid && cname) {
        selectedClassId.value = cid
        classOptions.value = [{ id: cid, name: cname }]
      }
    } else {
      // 年级组长/超管: 加载所有班级
      const res: any = await getClasses({ page_size: 200 })
      const items = res?.items ?? res?.data ?? res ?? []
      classOptions.value = items.map((c: any) => ({
        id: c.id,
        name: c.name || c.class_name,
      }))
      // 默认选第一个
      if (classOptions.value.length > 0) {
        selectedClassId.value = classOptions.value[0].id
      }
    }
  } catch {
    // fallback: 使用 userInfo 的 class_id
    const cid = userStore.userInfo?.class_id
    const cname = userStore.userInfo?.class_name
    if (cid && cname) {
      selectedClassId.value = cid
      classOptions.value = [{ id: cid, name: cname }]
    }
  }
}

// ── 加载考勤历史 ──
async function loadHistory() {
  if (!selectedClassId.value || !dateRange.value) return

  isLoading.value = true
  try {
    const [start, end] = dateRange.value
    const startDate = formatDate(start)
    const endDate = formatDate(end)

    const res: any = await getClassAttendanceHistory(selectedClassId.value, startDate, endDate)
    // axios拦截器已解包response.data，直接拿数据
    const payload = res?.history ?? res?.data?.history ?? []
    historyData.value = payload as ClassHistoryMetric[]

    await nextTick()
    renderChart()
  } catch (err: any) {
    console.error('[ClassHistoryDashboard] loadHistory failed:', err)
    historyData.value = []
  } finally {
    isLoading.value = false
  }
}

function formatDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

// ── 事件处理 ──
function handleClassChange() {
  loadHistory()
}

function handleDateChange() {
  loadHistory()
}

// ── ECharts 渲染 ──
function renderChart() {
  if (!trendChartRef.value) return

  if (!trendChart) {
    trendChart = echarts.init(trendChartRef.value)
  }

  const dates = historyData.value.map(d => d.date)
  const rates = historyData.value.map(d => d.attendance_rate)
  const lateData = historyData.value.map(d => d.late)
  const earlyData = historyData.value.map(d => d.early)
  const absentData = historyData.value.map(d => d.absent)
  const leaveData = historyData.value.map(d => d.leave)

  const option: any = {
    backgroundColor: BG_PRIMARY,
    title: {
      text: '考勤全息动态走势',
      subtext: `${classOptions.value.find(c => c.id === selectedClassId.value)?.name || ''} — 双轴复合: 出勤率折线 + 异常人次堆叠`,
      left: 'center',
      textStyle: { color: TEXT_PRIMARY, fontSize: 16 },
      subtextStyle: { color: TEXT_SECONDARY, fontSize: 12 },
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: BG_SECONDARY,
      borderColor: BORDER_COLOR,
      textStyle: { color: TEXT_PRIMARY },
      axisPointer: { type: 'cross', crossStyle: { color: TEXT_SECONDARY } },
      formatter(params: any) {
        const idx = params[0]?.dataIndex ?? 0
        const d = historyData.value[idx]
        if (!d) return ''
        let html = `<b>${d.date}</b><br/>`
        html += `出勤率: <span style="color:${ACCENT_BLUE}">${d.attendance_rate.toFixed(1)}%</span><br/>`
        html += `总人数: ${d.total} | 出勤: ${d.present}<br/>`
        html += `<span style="color:${ACCENT_RED}">缺勤(CRITICAL): ${d.absent}</span><br/>`
        html += `<span style="color:${ACCENT_YELLOW}">迟到: ${d.late} | 早退: ${d.early}</span><br/>`
        html += `<span style="color:${ACCENT_GREEN}">请假(INFO): ${d.leave}</span><br/>`
        html += `扣分: <span style="color:${ACCENT_RED}">−${calcDeduction(d)}</span>`
        return html
      },
    },
    legend: {
      top: 60,
      textStyle: { color: TEXT_SECONDARY },
      data: ['出勤率', '缺勤(CRITICAL)', '迟到', '早退', '请假(INFO)'],
    },
    toolbox: {
      right: 20,
      top: 10,
      feature: {
        dataZoom: { yAxisIndex: 'none' },
        restore: {},
        saveAsImage: { backgroundColor: BG_PRIMARY },
      },
      iconStyle: { borderColor: TEXT_SECONDARY },
    },
    grid: {
      left: 60,
      right: 60,
      top: 100,
      bottom: 80,
    },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { color: TEXT_SECONDARY, fontSize: 10, rotate: dates.length > 30 ? 30 : 0 },
      axisLine: { lineStyle: { color: BORDER_COLOR } },
      axisTick: { lineStyle: { color: BORDER_COLOR } },
    },
    yAxis: [
      {
        type: 'value',
        name: '出勤率(%)',
        nameTextStyle: { color: TEXT_SECONDARY },
        min: 0,
        max: 100,
        axisLabel: { color: TEXT_SECONDARY, formatter: '{value}%' },
        axisLine: { lineStyle: { color: ACCENT_BLUE } },
        splitLine: { lineStyle: { color: BORDER_COLOR, type: 'dashed' } },
      },
      {
        type: 'value',
        name: '异常人次',
        nameTextStyle: { color: TEXT_SECONDARY },
        min: 0,
        axisLabel: { color: TEXT_SECONDARY },
        axisLine: { lineStyle: { color: ACCENT_YELLOW } },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      {
        type: 'slider',
        start: 0,
        end: 100,
        height: 30,
        bottom: 10,
        borderColor: BORDER_COLOR,
        fillerColor: 'rgba(88,166,255,0.15)',
        handleStyle: { color: ACCENT_BLUE },
        textStyle: { color: TEXT_SECONDARY },
        dataBackground: {
          lineStyle: { color: ACCENT_BLUE },
          areaStyle: { color: 'rgba(88,166,255,0.1)' },
        },
      },
    ],
    series: [
      {
        name: '出勤率',
        type: 'line',
        yAxisIndex: 0,
        data: rates,
        smooth: true,
        symbol: 'circle',
        symbolSize: 6,
        lineStyle: { color: ACCENT_BLUE, width: 3 },
        itemStyle: { color: ACCENT_BLUE },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(88,166,255,0.25)' },
            { offset: 1, color: 'rgba(88,166,255,0.02)' },
          ]),
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: ACCENT_YELLOW, type: 'dashed' },
          data: [{ yAxis: 90, label: { position: 'end', formatter: '90%警戒线', color: ACCENT_YELLOW } }],
        },
      },
      {
        name: '缺勤(CRITICAL)',
        type: 'bar',
        yAxisIndex: 1,
        stack: 'abnormal',
        data: absentData,
        itemStyle: { color: ACCENT_RED },
        barWidth: dates.length > 60 ? 4 : dates.length > 30 ? 8 : 12,
      },
      {
        name: '迟到',
        type: 'bar',
        yAxisIndex: 1,
        stack: 'abnormal',
        data: lateData,
        itemStyle: { color: ACCENT_YELLOW },
        barWidth: dates.length > 60 ? 4 : dates.length > 30 ? 8 : 12,
      },
      {
        name: '早退',
        type: 'bar',
        yAxisIndex: 1,
        stack: 'abnormal',
        data: earlyData,
        itemStyle: { color: ACCENT_PURPLE },
        barWidth: dates.length > 60 ? 4 : dates.length > 30 ? 8 : 12,
      },
      {
        name: '请假(INFO)',
        type: 'bar',
        yAxisIndex: 1,
        stack: 'abnormal',
        data: leaveData,
        itemStyle: { color: ACCENT_GREEN },
        barWidth: dates.length > 60 ? 4 : dates.length > 30 ? 8 : 12,
      },
    ],
  }

  trendChart.setOption(option, true)
}

// ── 窗口resize ──
function handleResize() {
  trendChart?.resize()
}

// ── 生命周期 ──
onMounted(async () => {
  await initClassOptions()
  if (selectedClassId.value) {
    await loadHistory()
  }
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  trendChart?.dispose()
  trendChart = null
})
</script>

<style scoped>
.class-history-dashboard {
  min-height: 100vh;
  padding: 20px;
  background: #0d1117;
  color: #e6edf3;
}

/* ── 控制栏 ── */
.control-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  margin-bottom: 16px;
}

.control-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.control-right {
  display: flex;
  align-items: center;
}

.class-selector {
  width: 200px;
}

.class-selector :deep(.el-input__wrapper) {
  background: #0d1117;
  border-color: #30363d;
  box-shadow: none;
}

.class-selector :deep(.el-input__inner) {
  color: #e6edf3;
}

.date-range-picker :deep(.el-input__wrapper) {
  background: #0d1117;
  border-color: #30363d;
  box-shadow: none;
}

.date-range-picker :deep(.el-input__inner) {
  color: #e6edf3;
}

.date-range-picker :deep(.el-range-separator) {
  color: #8b949e;
}

.formula-tag {
  font-size: 12px;
}

.formula-tooltip {
  font-size: 13px;
  line-height: 1.6;
}

/* ── 暗色下拉菜单 ── */
:deep(.el-select-dropdown) {
  background: #161b22 !important;
  border-color: #30363d !important;
}

:deep(.el-select-dropdown__item) {
  color: #8b949e !important;
}

:deep(.el-select-dropdown__item.hover),
:deep(.el-select-dropdown__item:hover) {
  background: #30363d !important;
  color: #e6edf3 !important;
}

:deep(.el-select-dropdown__item.selected) {
  color: #58a6ff !important;
}

/* ── 暗色日期选择器弹窗 ── */
:deep(.el-date-editor) .el-picker-panel {
  background: #161b22;
  border-color: #30363d;
}

/* ── 数据摘要卡片 ── */
.summary-cards {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.summary-card {
  padding: 16px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  text-align: center;
}

.card-label {
  font-size: 13px;
  color: #8b949e;
  margin-bottom: 8px;
}

.card-value {
  font-size: 24px;
  font-weight: 700;
  color: #e6edf3;
}

.rate-value {
  color: #58a6ff;
}

.critical-value {
  color: #f85149;
}

.warning-value {
  color: #d29922;
}

.info-value {
  color: #3fb950;
}

/* ── 空状态 ── */
.empty-state {
  padding: 60px 0;
  text-align: center;
}

.empty-state :deep(.el-empty__description p) {
  color: #8b949e;
}

/* ── 加载状态 ── */
.loading-state {
  padding: 40px 20px;
}

/* ── 图表容器 ── */
.chart-container {
  margin-bottom: 16px;
}

.trend-chart {
  width: 100%;
  height: 480px;
  background: #0d1117;
  border: 1px solid #30363d;
  border-radius: 8px;
}

/* ── 异常明细表 ── */
.detail-section {
  margin-top: 8px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px 8px 0 0;
  font-size: 14px;
  font-weight: 600;
  color: #f85149;
}

.abnormal-table {
  background: #161b22;
}

.abnormal-table :deep(.el-table__header-wrapper th) {
  background: #30363d;
  color: #e6edf3;
}

.abnormal-table :deep(.el-table__row) {
  background: #161b22;
}

.abnormal-table :deep(.el-table__row:hover > td) {
  background: #30363d !important;
}

.abnormal-table :deep(.el-table__body-wrapper) {
  background: #161b22;
}

.abnormal-table :deep(td) {
  color: #e6edf3;
  border-bottom-color: #30363d;
}

.abnormal-table :deep(.el-table__empty-block) {
  background: #161b22;
}

.text-critical {
  color: #f85149;
  font-weight: 700;
}

.deduction-value {
  color: #f85149;
  font-weight: 600;
}

/* ── 响应式 ── */
@media (max-width: 768px) {
  .summary-cards {
    grid-template-columns: repeat(2, 1fr);
  }

  .control-bar {
    flex-direction: column;
    gap: 12px;
    align-items: flex-start;
  }

  .control-left {
    flex-wrap: wrap;
  }

  .trend-chart {
    height: 360px;
  }
}
</style>
