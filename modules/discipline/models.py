"""
modules/discipline/models.py — 处分登记模型

表: discipline_sanctions — 处分档案表（独立于 behavior_records）

生命周期状态机 (二级审批 + 闭环):
  DRAFT_PENDING → PENDING               (班主任确认提交)
  PENDING       → GRADE_LEADER_APPROVED (年级组长初审通过)
  GRADE_LEADER_APPROVED → ACTIVE        (德育处终审通过，生效扣分)
  PENDING / GRADE_LEADER_APPROVED → REJECTED (任一阶段驳回)
  ACTIVE        → PARENT_COMMUNICATED   (家长已沟通，家校闭环)
  PARENT_COMMUNICATED → CLOSED           (结案归档，处分生命周期终结)
  ACTIVE        → REVOKED               (表现良好，撤销处分)

与违纪溯源: behavior_record_id → behavior_records.id
自动化引擎: 30天滑窗 + 3次严重违纪 → 自动生成 DRAFT_PENDING 草稿
"""

from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, BigInteger, String, Date, DateTime,
    ForeignKey, Text, Boolean, Enum as SQLEnum, Index, JSON,
)
from sqlalchemy.orm import relationship
from core.models import Base, SchoolMixin, get_local_now
import enum


# ═══════════════════════════════════════════════════════════════
# 枚举定义
# ═══════════════════════════════════════════════════════════════

class DisciplineLevel(str, enum.Enum):
    """处分等级 — 按行政严重性递增"""
    WARNING = "WARNING"               # 警告
    SERIOUS_WARNING = "SERIOUS_WARN"  # 严重警告
    DEMERIT = "DEMERIT"               # 记过
    PROBATION = "PROBATION"           # 留校察看
    EXPULSION = "EXPULSION"           # 开除学籍（极低频，保留定义）


class DisciplineStatus(str, enum.Enum):
    """处分生命周期状态 — 二级审批流"""
    DRAFT_PENDING = "DRAFT_PENDING"          # 系统自动生成的处分草稿（班主任可见，待确认提交）
    PENDING = "PENDING"                      # 待年级组长初审（班主任已提交）
    GRADE_LEADER_APPROVED = "GRADE_LEADER_APPROVED"  # 年级组长初审通过，待德育处终审
    ACTIVE = "ACTIVE"                        # 德育处终审通过，正式生效
    REJECTED = "REJECTED"                    # 审批驳回（年级组长或德育处均可驳回），归档留痕
    REVOKED = "REVOKED"                      # 处分被撤销／解除（表现良好申请通过）
    # ↓ 家校闭环（G5 port）：必须追加在末尾 —— MySQL ENUM 按位置存索引，
    #   插在中间会让既有 REJECTED/REVOKED 的索引错位，造成静默数据损坏
    PARENT_COMMUNICATED = "PARENT_COMMUNICATED"  # 家长已沟通（家校闭环，处分进入待结案）
    CLOSED = "CLOSED"                        # 已结案归档（处分生命周期终结）


# 处分等级 → 中文标签
LEVEL_LABELS = {
    DisciplineLevel.WARNING: "警告",
    DisciplineLevel.SERIOUS_WARNING: "严重警告",
    DisciplineLevel.DEMERIT: "记过",
    DisciplineLevel.PROBATION: "留校察看",
    DisciplineLevel.EXPULSION: "开除学籍",
}

# 处分等级 → 评价值扣减（阶梯熔断模型）
LEVEL_PENALTY_MAP = {
    DisciplineLevel.WARNING: -5,
    DisciplineLevel.SERIOUS_WARNING: -10,
    DisciplineLevel.DEMERIT: -20,
    DisciplineLevel.PROBATION: None,   # None = 一票否决（不扣分，直接标记不合格）
    DisciplineLevel.EXPULSION: None,   # None = 开除（不在评价体系内）
}

# 触发一票否决的处分等级
VETO_LEVELS = {DisciplineLevel.PROBATION, DisciplineLevel.EXPULSION}

# 连续大错自动升级阈值：单学期 ACTIVE 处分次数 → 自动升级到更高等级
AUTO_ESCALATION_MAP = {
    2: DisciplineLevel.SERIOUS_WARNING,  # 2 次警告 → 严重警告
    3: DisciplineLevel.DEMERIT,          # 3 次（严重）警告 → 记过
    5: DisciplineLevel.PROBATION,        # 5 次处分 → 留校察看
}


# ═══════════════════════════════════════════════════════════════
# 处分档案表
# ═══════════════════════════════════════════════════════════════

class DisciplineSanction(Base, SchoolMixin):
    """
    处分历史档案表 — Wings 3.0 原生设计

    「违纪」定义为日常行为记录（behavior_records），
    「处分」定义为经过行政审批程序后下发的正式行政裁定（discipline_sanctions）。

    核心关联:
      - behavior_record_id: 若处分由某次严重违纪触发，则建立溯源钢印
      - student_id: 被处分学生
      - creator_id: 提报人（通常为班主任）
      - approver_id: 审批人（德育处管理员）
    """
    __tablename__ = "discipline_sanctions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # ── 核心关联 ──
    student_id = Column(BigInteger, ForeignKey("students.id"), nullable=False, index=True,
                        comment="被处分学生")
    class_id = Column(BigInteger, ForeignKey("classes.id"), nullable=False, index=True,
                      comment="学生所在班级（冗余，加速查询）")
    grade_id = Column(BigInteger, ForeignKey("grades.id"), nullable=False, index=True,
                      comment="学生所在年级（冗余，加速查询）")

    # 🎯 溯源钢印: 关联导致处分的严重违纪记录
    # NULL 允许历史孤立数据（旧系统迁移的纯文本处分）
    behavior_record_id = Column(
        BigInteger, ForeignKey("discipline_records.id"), nullable=True, index=True,
        comment="溯源: 关联的严重违纪记录(behavior_records)"
    )

    # ── 处分核心字段 ──
    level = Column(SQLEnum(DisciplineLevel), nullable=False, comment="处分等级")
    status = Column(
        SQLEnum(DisciplineStatus), nullable=False,
        default=DisciplineStatus.PENDING, index=True,
        comment="生命周期状态: DRAFT_PENDING/PENDING/GRADE_LEADER_APPROVED/ACTIVE/PARENT_COMMUNICATED/CLOSED/REJECTED/REVOKED"
    )
    reason = Column(Text, nullable=False, comment="处分事由")
    document_no = Column(String(50), nullable=True, comment="德育处红头文件编号 (如: 梨中德字[2026]05号)")

    # ── Phase 2 自动化引擎字段 ──
    evidence_snapshot = Column(
        JSON, nullable=True,
        comment="铁证快照(MYSQL8原生JSON): 触发升级的前N次严重违纪，含id/时间/地点/详情"
    )
    auto_generated = Column(
        Boolean, default=False, nullable=False,
        comment="是否由30天滑窗引擎自动生成（区别于班主任手动提报）"
    )

    # ── 时间线索 ──
    punish_date = Column(Date, nullable=False, default=date.today,
                         comment="处分下发日期（审批通过当天）")
    revoke_date = Column(Date, nullable=True, comment="撤销日期")
    revoke_reason = Column(Text, nullable=True, comment="撤销原因／改过评语")

    # ── 操作人追踪 ──
    creator_id = Column(BigInteger, ForeignKey("users.id"), nullable=True,
                        comment="提报人（班主任），系统自动生成时为 NULL")
    approver_id = Column(BigInteger, ForeignKey("users.id"), nullable=True,
                         comment="审批人（德育主任）")

    # ── 二级审批审计追踪 ──
    grade_leader_id = Column(
        BigInteger, ForeignKey("users.id"), nullable=True,
        comment="初审人（年级组长）"
    )
    grade_leader_comment = Column(Text, nullable=True, comment="初审意见")
    grade_leader_reviewed_at = Column(DateTime, nullable=True, comment="初审时间")

    approver_comment = Column(Text, nullable=True, comment="终审意见（德育处）")

    created_at = Column(DateTime, default=get_local_now)
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)

    # ── 关系 ──
    student = relationship("core.models.Student", lazy="selectin")
    class_ = relationship("core.models.Class", lazy="selectin")
    grade = relationship("core.models.Grade", lazy="selectin")
    creator = relationship("core.models.User", foreign_keys=[creator_id], lazy="selectin")
    approver = relationship("core.models.User", foreign_keys=[approver_id], lazy="selectin")
    grade_leader = relationship("core.models.User", foreign_keys=[grade_leader_id], lazy="selectin")
    # behavior_record 可选 — 仅在存在溯源的处分上加载
    behavior_record = relationship(
        "modules.behavior.models.DisciplineRecord",
        foreign_keys=[behavior_record_id], lazy="selectin")

    __table_args__ = (
        Index("idx_ds_school_student", "school_id", "student_id"),
        Index("idx_ds_school_status", "school_id", "status"),
        Index("idx_ds_class_date", "class_id", "punish_date"),
    )


# ═══════════════════════════════════════════════════════════════
# 申诉状态枚举
# ═══════════════════════════════════════════════════════════════

class AppealStatus(str, enum.Enum):
    """家校申诉复核状态"""
    PENDING = "PENDING"      # 待复核
    ACCEPTED = "ACCEPTED"    # 申诉通过（→ 撤销处分或降级）
    REJECTED = "REJECTED"    # 申诉驳回


# 申诉状态 → 中文标签
APPEAL_STATUS_LABELS = {
    AppealStatus.PENDING: "待复核",
    AppealStatus.ACCEPTED: "申诉通过",
    AppealStatus.REJECTED: "申诉驳回",
}


# ═══════════════════════════════════════════════════════════════
# 家校申诉表
# ═══════════════════════════════════════════════════════════════

class SanctionAppeal(Base, SchoolMixin):
    """
    家校申诉记录表 — Phase 4 Webhook 驱动

    家长通过外部系统（微信小程序等）对已生效处分提交申诉，
    Wings 3.0 接收 Webhook 后写入本表，德育处管理员进行复核。

    复核结果联动处分状态:
      - ACCEPTED → 自动撤销原处分（ACTIVE → REVOKED）或降级
      - REJECTED → 驳回，原处分维持

    幂等保护: idempotency_key 保证同一次外部请求不会重复创建。
    """
    __tablename__ = "sanction_appeals"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # ── 关联 ──
    sanction_id = Column(
        BigInteger, ForeignKey("discipline_sanctions.id"), nullable=False, index=True,
        comment="被申诉的处分记录"
    )

    # ── 申请人信息（来自外部系统，非本系统 user） ──
    applicant_name = Column(String(50), nullable=False, comment="申诉人姓名（家长）")
    applicant_phone = Column(String(20), nullable=True, comment="申诉人联系电话")

    # ── 申诉内容 ──
    reason = Column(Text, nullable=False, comment="申诉事由")

    # ── 幂等键 ──
    idempotency_key = Column(
        String(100), nullable=False, unique=True, index=True,
        comment="外部系统幂等键，防重复提交"
    )

    # ── 复核状态机 ──
    status = Column(
        SQLEnum(AppealStatus), nullable=False, default=AppealStatus.PENDING, index=True,
        comment="复核状态: PENDING/ACCEPTED/REJECTED"
    )

    # ── 复核审计 ──
    reviewer_id = Column(
        BigInteger, ForeignKey("users.id"), nullable=True,
        comment="复核人（德育处管理员）"
    )
    review_comment = Column(Text, nullable=True, comment="复核意见")
    reviewed_at = Column(DateTime, nullable=True, comment="复核时间")

    # ── 时间戳 ──
    created_at = Column(DateTime, default=get_local_now)
    updated_at = Column(DateTime, default=get_local_now, onupdate=get_local_now)

    # ── 关系 ──
    sanction = relationship("DisciplineSanction", lazy="selectin")
    reviewer = relationship("core.models.User", foreign_keys=[reviewer_id], lazy="selectin")

    __table_args__ = (
        Index("idx_sa_school_status", "school_id", "status"),
        Index("idx_sa_sanction", "sanction_id"),
    )
