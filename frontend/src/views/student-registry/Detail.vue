<template>
  <div class="student-detail-page">
    <!-- 返回栏 -->
    <div class="back-bar">
      <el-button :icon="ArrowLeft" @click="$router.push('/student-registry')">返回列表</el-button>
      <h2 class="page-title">学籍详情</h2>
      <!-- 旧系统数据映射浮标 -->
      <div v-if="student?.sync_status === 'legacy'" class="legacy-badge">
        <el-tag type="warning" effect="dark" size="large">
          <el-icon><Warning /></el-icon>
          遗留系统数据 — 由旧系统迁移
        </el-tag>
      </div>
    </div>

    <!-- 加载 -->
    <div v-if="loading" class="loading-wrap">
      <el-skeleton :rows="10" animated />
    </div>

    <template v-if="!loading && student">
      <!-- 基本信息卡片 -->
      <el-row :gutter="16">
        <el-col :span="16">
          <el-card shadow="hover" class="info-card">
            <template #header>
              <div class="card-header">
                <span class="card-title">学生基本信息</span>
                <el-button type="primary" size="small" :icon="Edit" @click="editMode = true">编辑</el-button>
              </div>
            </template>

            <!-- 查看模式 -->
            <template v-if="!editMode">
              <el-descriptions :column="3" border size="small">
                <el-descriptions-item label="学号">{{ student.student_no }}</el-descriptions-item>
                <el-descriptions-item label="姓名">{{ student.name }}</el-descriptions-item>
                <el-descriptions-item label="性别">{{ student.gender === 'M' ? '男' : student.gender === 'F' ? '女' : '-' }}</el-descriptions-item>
                <el-descriptions-item label="班级">{{ student.class_name || '-' }}</el-descriptions-item>
                <el-descriptions-item label="年级">{{ student.grade_name || '-' }}</el-descriptions-item>
                <el-descriptions-item label="状态">
                  <el-tag :type="statusTagType(student.registry_status)" size="small">
                    {{ REGISTRY_STATUS_LABELS[student.registry_status] || student.registry_status || '在读' }}
                  </el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="出生日期">{{ student.birth_date || '-' }}</el-descriptions-item>
                <el-descriptions-item label="民族">{{ student.nationality || '-' }}</el-descriptions-item>
                <el-descriptions-item label="身份证号">{{ student.id_card || '-' }}</el-descriptions-item>
                <el-descriptions-item label="入学日期">{{ student.enrolled_at || '-' }}</el-descriptions-item>
                <el-descriptions-item label="入学方式">{{ student.enrollment_type || '正常' }}</el-descriptions-item>
                <el-descriptions-item label="全国学籍号">{{ student.national_student_no || '-' }}</el-descriptions-item>
                <el-descriptions-item label="家庭地址" :span="3">{{ student.address || '-' }}</el-descriptions-item>
              </el-descriptions>
            </template>

            <!-- 编辑模式 -->
            <template v-else>
              <el-form :model="editForm" label-width="100px">
                <el-row :gutter="16">
                  <el-col :span="12"><el-form-item label="姓名"><el-input v-model="editForm.name" /></el-form-item></el-col>
                  <el-col :span="12">
                    <el-form-item label="性别">
                      <el-radio-group v-model="editForm.gender">
                        <el-radio value="M">男</el-radio>
                        <el-radio value="F">女</el-radio>
                      </el-radio-group>
                    </el-form-item>
                  </el-col>
                </el-row>
                <el-row :gutter="16">
                  <el-col :span="12"><el-form-item label="出生日期"><el-date-picker v-model="editForm.birth_date" type="date" style="width: 100%" /></el-form-item></el-col>
                  <el-col :span="12"><el-form-item label="民族"><el-input v-model="editForm.nationality" /></el-form-item></el-col>
                </el-row>
                <el-row :gutter="16">
                  <el-col :span="12"><el-form-item label="身份证号"><el-input v-model="editForm.id_card" maxlength="18" /></el-form-item></el-col>
                  <el-col :span="12"><el-form-item label="全国学籍号"><el-input v-model="editForm.national_student_no" /></el-form-item></el-col>
                </el-row>
                <el-form-item label="家庭地址"><el-input v-model="editForm.address" /></el-form-item>
              </el-form>
              <div style="text-align: right; margin-top: 16px">
                <el-button @click="editMode = false">取消</el-button>
                <el-button type="primary" :loading="saving" @click="doSave">保存</el-button>
              </div>
            </template>
          </el-card>
        </el-col>

        <!-- 状态变更操作区 -->
        <el-col :span="8">
          <el-card shadow="hover" class="action-card">
            <template #header><span class="card-title">状态变更</span></template>
            <div class="action-grid">
              <el-button
                v-if="student.registry_status === 'active'"
                type="warning"
                :icon="SwitchButton"
                @click="showStatusDialog('suspend')"
                block
              >休学</el-button>
              <el-button
                v-if="student.registry_status === 'suspended'"
                type="success"
                :icon="SwitchButton"
                @click="showStatusDialog('resume')"
                block
              >复学</el-button>
              <el-button
                v-if="student.registry_status === 'active' || student.registry_status === 'suspended'"
                type="primary"
                :icon="Promotion"
                @click="showStatusDialog('transfer')"
                block
              >转学</el-button>
              <el-button
                v-if="student.registry_status === 'active'"
                type="info"
                :icon="Medal"
                @click="showStatusDialog('graduate')"
                block
              >毕业</el-button>
            </div>

            <el-divider />

            <!-- 当前状态 -->
            <div class="current-status" v-if="student.registry_status !== 'active'">
              <el-alert
                :title="`当前状态: ${REGISTRY_STATUS_LABELS[student.registry_status] || student.registry_status}`"
                :type="student.registry_status === 'suspended' ? 'warning' : 'info'"
                show-icon
                :closable="false"
              />
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 家庭背景 -->
      <el-card shadow="hover" class="info-card" style="margin-top: 16px">
        <template #header><span class="card-title">家庭信息</span></template>
        <template v-if="editMode">
          <el-row :gutter="16">
            <el-col :span="8"><el-form-item label="家长1姓名"><el-input v-model="editForm.parent1_name" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="电话"><el-input v-model="editForm.parent1_phone" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="关系"><el-input v-model="editForm.parent1_relation" /></el-form-item></el-col>
          </el-row>
          <el-row :gutter="16">
            <el-col :span="8"><el-form-item label="家长2姓名"><el-input v-model="editForm.parent2_name" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="电话"><el-input v-model="editForm.parent2_phone" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="关系"><el-input v-model="editForm.parent2_relation" /></el-form-item></el-col>
          </el-row>
        </template>
        <template v-else>
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="家长1姓名">{{ student.parent1_name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="电话">{{ student.parent1_phone || '-' }}</el-descriptions-item>
            <el-descriptions-item label="关系">{{ student.parent1_relation || '-' }}</el-descriptions-item>
            <el-descriptions-item label="家长2姓名">{{ student.parent2_name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="电话">{{ student.parent2_phone || '-' }}</el-descriptions-item>
            <el-descriptions-item label="关系">{{ student.parent2_relation || '-' }}</el-descriptions-item>
          </el-descriptions>
        </template>
      </el-card>

      <!-- 学籍变更轨迹 -->
      <el-card shadow="hover" class="info-card" style="margin-top: 16px">
        <template #header>
          <div class="card-header">
            <span class="card-title">学籍变更轨迹</span>
            <span class="card-count">{{ history.length }} 条记录</span>
          </div>
        </template>
        <el-timeline v-if="history.length">
          <el-timeline-item
            v-for="h in history"
            :key="h.id"
            :timestamp="h.created_at ? new Date(h.created_at).toLocaleString() : '-'"
            :color="historyColor(h.change_type)"
          >
            <div class="history-item">
              <div class="history-header">
                <el-tag :type="historyTagType(h.change_type)" size="small">
                  {{ changeTypeLabel(h.change_type) }}
                </el-tag>
                <span class="history-operator">操作人: {{ h.operator_name || '-' }}</span>
              </div>
              <div v-if="h.reason" class="history-reason">原因: {{ h.reason }}</div>
              <div v-if="h.target_school" class="history-extra">转入学校: {{ h.target_school }}</div>
              <div v-if="h.expected_resume_date" class="history-extra">预计复学: {{ h.expected_resume_date }}</div>
              <div v-if="h.remark" class="history-remark">{{ h.remark }}</div>
            </div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无变更记录" :image-size="80" />
      </el-card>
    </template>

    <!-- 状态变更弹窗 -->
    <el-dialog
      v-model="statusDialogVisible"
      :title="statusDialogTitle"
      width="460px"
      destroy-on-close
    >
      <el-form :model="statusForm" label-width="90px">
        <el-form-item v-if="statusType === 'transfer'" label="转入学校">
          <el-input v-model="statusForm.target_school" placeholder="请输入转入学校名称" />
        </el-form-item>
        <el-form-item v-if="statusType === 'suspend'" label="预计复学">
          <el-date-picker v-model="statusForm.expected_resume_date" type="date" style="width: 100%" />
        </el-form-item>
        <el-form-item label="原因" required>
          <el-input v-model="statusForm.reason" type="textarea" :rows="3" :placeholder="statusFormPlaceholder" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="statusForm.remark" placeholder="额外备注（选填）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="statusDialogVisible = false">取消</el-button>
        <el-button :type="statusConfirmBtnType" :loading="statusSubmitting" @click="doStatusChange">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Edit, SwitchButton, Promotion, Medal, Warning } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  getStudent, updateStudent,
  transferStudent, suspendStudent, resumeStudent, graduateStudent,
  getStatusHistory,
  REGISTRY_STATUS_LABELS,
  type StudentDetail,
  type StatusChangeRecord,
  type StatusChangeType,
} from '@/api/students'

const route = useRoute()
const router = useRouter()

// ── 状态 ──
const loading = ref(true)
const student = ref<StudentDetail | null>(null)
const editMode = ref(false)
const saving = ref(false)
const editForm = ref<any>({})
const history = ref<StatusChangeRecord[]>([])

// 状态变更弹窗
const statusDialogVisible = ref(false)
const statusType = ref<StatusChangeType>('suspend')
const statusSubmitting = ref(false)
const statusForm = ref({ reason: '', target_school: '', expected_resume_date: null as any, remark: '' })

// ── 计算 ──
const statusDialogTitle = computed(() => {
  const m: Record<string, string> = { suspend: '确认休学', resume: '确认复学', transfer: '确认转学', graduate: '确认毕业' }
  return m[statusType.value] || '状态变更'
})
const statusFormPlaceholder = computed(() => {
  const m: Record<string, string> = { suspend: '请输入休学原因...', resume: '请输入复学原因...', transfer: '请输入转学原因...', graduate: '请输入毕业备注...' }
  return m[statusType.value] || ''
})
const statusConfirmBtnType = computed(() => {
  const m: Record<string, string> = { suspend: 'warning', resume: 'success', transfer: 'primary', graduate: 'info' }
  return m[statusType.value] || 'primary'
})

// ── 标签 ──
function statusTagType(status?: string) {
  const m: Record<string, string> = { active: 'success', suspended: 'warning', transferred: 'info', graduated: '', inactive: 'danger' }
  return m[status || 'active'] || 'info'
}
function historyColor(type: string) {
  const m: Record<string, string> = { transfer: '#409eff', suspend: '#e6a23c', resume: '#67c23a', graduate: '#909399', inactive: '#f56c6c' }
  return m[type] || '#909399'
}
function historyTagType(type: string) {
  const m: Record<string, string> = { transfer: '', suspend: 'warning', resume: 'success', graduate: 'info', inactive: 'danger' }
  return m[type] || 'info'
}
function changeTypeLabel(type: string) {
  const m: Record<string, string> = { transfer: '转学', suspend: '休学', resume: '复学', graduate: '毕业', inactive: '离校' }
  return m[type] || type
}

// ── 加载 ──
async function loadStudent(id: number) {
  loading.value = true
  try {
    const [res, hist] = await Promise.all([
      getStudent(id).catch(() => null),
      getStatusHistory(id).catch(() => []),
    ])
    student.value = res as StudentDetail | null
    history.value = (hist as any)?.data || hist || []
    editForm.value = { ...student.value }
  } catch (e) {
    console.error('Load student detail error:', e)
  } finally {
    loading.value = false
  }
}

// ── 保存 ──
async function doSave() {
  if (!student.value) return
  saving.value = true
  try {
    const body = { ...editForm.value }
    if (body.birth_date instanceof Date) body.birth_date = body.birth_date.toISOString().slice(0, 10)
    else delete body.birth_date
    await updateStudent(student.value.id, body)
    ElMessage.success('保存成功')
    editMode.value = false
    loadStudent(student.value.id)
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

// ── 状态变更 ──
function showStatusDialog(type: StatusChangeType) {
  statusType.value = type
  statusForm.value = { reason: '', target_school: '', expected_resume_date: null, remark: '' }
  statusDialogVisible.value = true
}

async function doStatusChange() {
  if (!statusForm.value.reason) {
    ElMessage.warning('请填写原因')
    return
  }
  if (!student.value) return
  statusSubmitting.value = true
  try {
    const apiMap: Record<string, Function> = {
      transfer: transferStudent,
      suspend: suspendStudent,
      resume: resumeStudent,
      graduate: graduateStudent,
    }
    const fn = apiMap[statusType.value]
    if (!fn) return
    const body: any = { change_type: statusType.value, reason: statusForm.value.reason, remark: statusForm.value.remark }
    if (statusType.value === 'transfer') body.target_school = statusForm.value.target_school
    if (statusType.value === 'suspend' && statusForm.value.expected_resume_date) {
      body.expected_resume_date = new Date(statusForm.value.expected_resume_date).toISOString().slice(0, 10)
    }
    await fn(student.value.id, body)
    ElMessage.success(`${statusDialogTitle.value}完成`)
    statusDialogVisible.value = false
    loadStudent(student.value.id)
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
  } finally {
    statusSubmitting.value = false
  }
}

onMounted(() => {
  const id = Number(route.query.id)
  if (id) loadStudent(id)
  else router.push('/student-registry')
})
</script>

<style scoped>
.student-detail-page {
  padding: 20px;
  color: #c9d1d9;
}

.back-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.page-title { font-size: 18px; font-weight: 600; color: #f0f6fc; margin: 0; }

.legacy-badge {
  margin-left: auto;
}

.loading-wrap { padding: 40px; }

.info-card {
  background: #161b22 !important;
  border: 1px solid #30363d !important;
  height: 100%;
}
.info-card :deep(.el-card__header) {
  border-bottom: 1px solid #30363d;
  padding: 14px 20px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title { font-size: 15px; font-weight: 600; color: #f0f6fc; }
.card-count { font-size: 13px; color: #8b949e; }

.action-card {
  background: #161b22 !important;
  border: 1px solid #30363d !important;
  height: 100%;
}
.action-card :deep(.el-card__header) {
  border-bottom: 1px solid #30363d;
  padding: 14px 20px;
}
.action-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.current-status {
  margin-top: 12px;
}

.history-item {
  padding: 4px 0;
}
.history-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 6px;
}
.history-operator { font-size: 12px; color: #8b949e; }
.history-reason { font-size: 13px; color: #c9d1d9; }
.history-extra { font-size: 12px; color: #6e7681; margin-top: 2px; }
.history-remark { font-size: 12px; color: #6e7681; margin-top: 4px; font-style: italic; }
</style>
