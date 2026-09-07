"""
modules/discipline/services.py — 处分业务逻辑层

核心能力:
  1. 处分 CRUD（PENDING 状态可编辑，ACTIVE 后锁定）
  2. 行政审批状态机: PENDING → ACTIVE/REJECTED, ACTIVE → REVOKED
  3. 违纪一键升级: 累计扣分达到阈值 → 自动生成处分草案
  4. 处分自动扣分: ACTIVE 时按阶梯熔断模型扣除评价分
  5. 一票否决: PROBATION/EXPULSION 级别标记学生不合格
  6. 统计概览
"""

import logging
from datetime import date, timedelta

from core.event_bus import EventBus
from core.models import Class, Student, UserRole, get_local_now
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    AUTO_ESCALATION_MAP,
    LEVEL_LABELS,
    LEVEL_PENALTY_MAP,
    VETO_LEVELS,
    DisciplineLevel,
    DisciplineSanction,
    DisciplineStatus,
    SanctionAppeal,
)

logger = logging.getLogger(__name__)


# ── 状态 → 中文标签 ──
STATUS_LABELS = {
    "DRAFT_PENDING": "系统草稿",
    "PENDING": "待年级组长初审",
    "GRADE_LEADER_APPROVED": "年级组长已审，待终审",
    "ACTIVE": "生效中",
    "PARENT_COMMUNICATED": "家长已沟通",
    "REJECTED": "已驳回",
    "REVOKED": "已撤销",
    "CLOSED": "已结案",
}


def _dt_str(val) -> str | None:
    """时间 → ISO 字符串"""
    if val is None:
        return None
    return val.isoformat() if hasattr(val, "isoformat") else str(val)


class DisciplineService:
    """处分管理服务"""

    # ═══════════════════════════════════════════════════════════
    # CRUD
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    async def create_sanction(
        db: AsyncSession,
        school_id: int,
        data: dict,
        creator_id: int,
    ) -> DisciplineSanction:
        """班主任提报处分 → 初始状态 PENDING"""
        # 查找学生信息
        student = await db.scalar(
            select(Student).where(
                Student.id == data["student_id"],
                Student.school_id == school_id,
            )
        )
        if not student:
            raise ValueError(f"学生不存在: id={data['student_id']}")

        # 验证等级
        level = data["level"]
        try:
            level_enum = DisciplineLevel(level)
        except ValueError:
            valid = [lv.value for lv in DisciplineLevel]
            raise ValueError(f"无效的处分等级: {level}，有效值: {valid}")

        sanction = DisciplineSanction(
            school_id=school_id,
            student_id=student.id,
            class_id=student.class_id,
            grade_id=student.grade_id,
            level=level_enum,
            status=DisciplineStatus.PENDING,
            reason=data["reason"],
            document_no=data.get("document_no"),
            behavior_record_id=data.get("behavior_record_id"),
            punish_date=data.get("punish_date") or date.today(),
            creator_id=creator_id,
        )
        db.add(sanction)
        await db.flush()

        # 检查是否需要自动升级（单学期现有 ACTIVE 处分次数已达阈值）
        await DisciplineService._check_auto_escalation(db, student, sanction)

        await db.commit()
        # 🔔 通知年级组长
        await DisciplineService._notify_on_discipline_event(
            db, sanction, "pending", sender_id=creator_id
        )
        await db.commit()

        # ⚡ Wings 3.2 CEP: 处分事件广播 → growth/listeners 接收站
        _LEVEL_TO_BEHAVIOR = {
            "WARNING": "minor",
            "SERIOUS_WARN": "warning",
            "DEMERIT": "major",
            "PROBATION": "serious",
            "EXPULSION": "serious",
        }
        bus = EventBus()
        bus.publish(
            "behavior.disciplined",
            {
                "school_id": school_id,
                "student_id": student.id,
                "class_id": student.class_id,
                "category": level_enum.value,
                "level": _LEVEL_TO_BEHAVIOR.get(level_enum.value, "minor"),
                "deduction": data.get("deduction", 0),
                "title": f"处分[{LEVEL_LABELS.get(level_enum, level_enum.value)}]: {data['reason'][:60]}",
            },
        )
        logger.info(
            f"[discipline] CEP published: student={student.id} "
            f"level={level_enum.value} sanction={sanction.id}"
        )

        # 重查询加载关系
        sanction = await DisciplineService._reload_with_relations(db, sanction.id)
        return sanction

    @staticmethod
    async def get_sanction(db: AsyncSession, sanction_id: int) -> DisciplineSanction | None:
        return await _query_by_id(db, sanction_id)

    @staticmethod
    async def list_sanctions(
        db: AsyncSession,
        school_id: int,
        class_id: int | None = None,
        grade_id: int | None = None,
        student_id: int | None = None,
        level: str | None = None,
        status: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DisciplineSanction], int]:
        """分页查询处分列表"""
        conditions = [DisciplineSanction.school_id == school_id]
        if class_id:
            conditions.append(DisciplineSanction.class_id == class_id)
        if grade_id:
            conditions.append(DisciplineSanction.grade_id == grade_id)
        if student_id:
            conditions.append(DisciplineSanction.student_id == student_id)
        if level:
            try:
                conditions.append(DisciplineSanction.level == DisciplineLevel(level))
            except ValueError:
                pass
        if status:
            try:
                conditions.append(DisciplineSanction.status == DisciplineStatus(status))
            except ValueError:
                pass
        if start_date:
            conditions.append(DisciplineSanction.punish_date >= start_date)
        if end_date:
            conditions.append(DisciplineSanction.punish_date <= end_date)

        cnt = await db.scalar(
            select(func.count()).select_from(DisciplineSanction).where(*conditions)
        )
        total = int(cnt or 0)

        stmt = (
            select(DisciplineSanction)
            .options(
                selectinload(DisciplineSanction.student).selectinload(Student.class_),
                selectinload(DisciplineSanction.creator),
                selectinload(DisciplineSanction.approver),
                selectinload(DisciplineSanction.grade_leader),
                selectinload(DisciplineSanction.class_),
            )
            .where(*conditions)
            .order_by(DisciplineSanction.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    @staticmethod
    async def update_sanction(
        db: AsyncSession, sanction_id: int, data: dict
    ) -> DisciplineSanction | None:
        """编辑处分 — PENDING 或 DRAFT_PENDING 状态可编辑"""
        sanction = await _query_by_id(db, sanction_id)
        if not sanction:
            return None
        if sanction.status not in (DisciplineStatus.PENDING, DisciplineStatus.DRAFT_PENDING):
            raise ValueError("仅待初审/草稿状态的处分可编辑")

        for key in ("level", "reason", "document_no", "punish_date"):
            if key in data and data[key] is not None:
                if key == "level":
                    try:
                        setattr(sanction, key, DisciplineLevel(data[key]))
                    except ValueError:
                        pass
                else:
                    setattr(sanction, key, data[key])
        sanction.updated_at = get_local_now()
        await db.commit()
        await db.refresh(sanction)
        return sanction

    @staticmethod
    async def delete_sanction(db: AsyncSession, sanction_id: int) -> bool:
        """删除处分 — PENDING 或 DRAFT_PENDING 状态可删除"""
        sanction = await _query_by_id(db, sanction_id)
        if not sanction:
            return False
        if sanction.status not in (DisciplineStatus.PENDING, DisciplineStatus.DRAFT_PENDING):
            raise ValueError("仅待初审/草稿状态的处分可删除")
        # P1 修复: 先清理关联的申诉记录（FK 约束 sanction_appeals.sanction_id）
        await db.execute(delete(SanctionAppeal).where(SanctionAppeal.sanction_id == sanction_id))
        await db.delete(sanction)
        await db.commit()
        return True

    # ═══════════════════════════════════════════════════════════
    # 状态机: 二级审批 (PENDING → GL_APPROVED → ACTIVE)
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    async def approve_sanction(
        db: AsyncSession,
        sanction_id: int,
        comment: str,
        reviewer_id: int,
        reviewer_role: str,
    ) -> DisciplineSanction | None:
        """
        行政审批 — 角色感知二级审批流

        一级审批 (grade_leader):
          PENDING → GRADE_LEADER_APPROVED
          记录初审人、意见、时间

        二级审批 (ms_admin):
          GRADE_LEADER_APPROVED → ACTIVE
          记录终审人、意见，触发阶梯扣分/一票否决
        """
        sanction = await _query_by_id(db, sanction_id)
        if not sanction:
            return None

        now = get_local_now()

        # ── 一级审批: 年级组长 ──
        if reviewer_role == "grade_leader":
            if sanction.status != DisciplineStatus.PENDING:
                raise ValueError(
                    f"年级组长只能审批「待初审」状态的处分，当前状态: {sanction.status.value}"
                )
            sanction.status = DisciplineStatus.GRADE_LEADER_APPROVED
            sanction.grade_leader_id = reviewer_id
            sanction.grade_leader_comment = comment or None
            sanction.grade_leader_reviewed_at = now
            sanction.updated_at = now
            # 🔔 通知德育处管理员
            await DisciplineService._notify_on_discipline_event(
                db, sanction, "gl_approved", sender_id=reviewer_id
            )
            logger.info(
                f"📋 年级组长初审通过: student_id={sanction.student_id} "
                f"level={sanction.level.value} id={sanction.id} "
                f"→ GRADE_LEADER_APPROVED, 待德育处终审"
            )

        # ── 二级审批: 德育处 ──
        elif reviewer_role == "ms_admin":
            if sanction.status != DisciplineStatus.GRADE_LEADER_APPROVED:
                raise ValueError(
                    f"德育处只能审批「待终审」状态的处分，当前状态: {sanction.status.value}"
                )
            sanction.status = DisciplineStatus.ACTIVE
            sanction.approver_id = reviewer_id
            sanction.approver_comment = comment or None
            sanction.updated_at = now
            # 🎯 终审通过即生效 → 联动评价引擎扣分／一票否决
            await DisciplineService._apply_penalty(db, sanction)
            # 🔔 通知班主任
            await DisciplineService._notify_on_discipline_event(
                db, sanction, "activated", sender_id=reviewer_id
            )
            logger.info(
                f"✅ 德育处终审通过: student_id={sanction.student_id} "
                f"level={sanction.level.value} id={sanction.id} "
                f"→ ACTIVE, 处分正式生效"
            )

        else:
            raise ValueError(f"无效审批角色: {reviewer_role}，有效值: grade_leader / ms_admin")

        await db.commit()
        await db.refresh(sanction)
        return sanction

    @staticmethod
    async def reject_sanction(
        db: AsyncSession,
        sanction_id: int,
        comment: str,
        reviewer_id: int,
        reviewer_role: str,
    ) -> DisciplineSanction | None:
        """
        行政审批驳回 — 任意阶段均可驳回

        年级组长驳回:
          PENDING → REJECTED (记录初审人为驳回人)

        德育处驳回:
          GRADE_LEADER_APPROVED → REJECTED (记录终审人为驳回人)
        """
        sanction = await _query_by_id(db, sanction_id)
        if not sanction:
            return None

        now = get_local_now()

        # ── 年级组长驳回 ──
        if reviewer_role == "grade_leader":
            if sanction.status != DisciplineStatus.PENDING:
                raise ValueError(
                    f"年级组长只能驳回「待初审」状态的处分，当前状态: {sanction.status.value}"
                )
            sanction.status = DisciplineStatus.REJECTED
            sanction.grade_leader_id = reviewer_id
            sanction.grade_leader_comment = comment or None
            sanction.grade_leader_reviewed_at = now
            sanction.updated_at = now
            # 🔔 通知班主任（年级组长驳回）
            await DisciplineService._notify_on_discipline_event(
                db, sanction, "rejected_by_gl", sender_id=reviewer_id, extra=comment
            )
            logger.info(
                f"❌ 年级组长驳回: student_id={sanction.student_id} "
                f"level={sanction.level.value} id={sanction.id}"
            )

        # ── 德育处驳回 ──
        elif reviewer_role == "ms_admin":
            if sanction.status != DisciplineStatus.GRADE_LEADER_APPROVED:
                raise ValueError(
                    f"德育处只能驳回「待终审」状态的处分，当前状态: {sanction.status.value}"
                )
            sanction.status = DisciplineStatus.REJECTED
            sanction.approver_id = reviewer_id
            sanction.approver_comment = comment or None
            sanction.updated_at = now
            # 🔔 通知班主任 + 年级组长（德育处驳回）
            await DisciplineService._notify_on_discipline_event(
                db, sanction, "rejected_by_ms", sender_id=reviewer_id, extra=comment
            )
            logger.info(
                f"❌ 德育处驳回: student_id={sanction.student_id} "
                f"level={sanction.level.value} id={sanction.id}"
            )

        else:
            raise ValueError(f"无效审批角色: {reviewer_role}，有效值: grade_leader / ms_admin")

        await db.commit()
        await db.refresh(sanction)
        return sanction

    # ═══════════════════════════════════════════════════════════
    # 状态机: 撤销 (ACTIVE → REVOKED)
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    async def revoke_sanction(
        db: AsyncSession,
        sanction_id: int,
        revoke_reason: str,
        revoke_date: date | None = None,
    ) -> DisciplineSanction | None:
        """
        撤销处分 — ACTIVE → REVOKED

        撤销后:
          - 处分历史保留（不可删）
          - 一票否决标记解除（如果此处分是唯一否决来源）
          - 处分扣分不回溯（历史分值保留，撤销是未来的正面修正）
        """
        sanction = await _query_by_id(db, sanction_id)
        if not sanction:
            return None
        if sanction.status != DisciplineStatus.ACTIVE:
            raise ValueError(f"只能撤销生效中的处分，当前状态: {sanction.status.value}")

        sanction.status = DisciplineStatus.REVOKED
        sanction.revoke_reason = revoke_reason
        sanction.revoke_date = revoke_date or date.today()
        sanction.updated_at = get_local_now()

        # 撤销一票否决标记（如果有）
        await DisciplineService._lift_veto_if_single(db, sanction)
        # ═══ PolicyEngine Hook-4: 处分撤销→通道A 100%回血 ═══
        await DisciplineService._apply_revocation_recovery(db, sanction)
        # 🔔 通知多方（撤销）
        await DisciplineService._notify_on_discipline_event(
            db, sanction, "revoked", extra=revoke_reason
        )

        await db.commit()
        await db.refresh(sanction)
        logger.info(
            f"🔄 处分已撤销: student_id={sanction.student_id} "
            f"level={sanction.level.value} id={sanction.id}"
        )
        return sanction

    # ═══════════════════════════════════════════════════════════
    # 状态机: 家长沟通 (ACTIVE → PARENT_COMMUNICATED)
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    async def mark_parent_communicated(
        db: AsyncSession,
        sanction_id: int,
    ) -> DisciplineSanction | None:
        """
        家长沟通完成 — ACTIVE → PARENT_COMMUNICATED

        家校闭环关键节点: 处分生效后，责任教师/年级/德育处
        与家长完成沟通，标记进入「待结案」状态。
        注: 沟通内容留痕列 (parent_communicated_at/note) 属 Stable Release
            后增强，本步仅推进状态机 + updated_at 时间戳。
        """
        sanction = await _query_by_id(db, sanction_id)
        if not sanction:
            return None
        if sanction.status != DisciplineStatus.ACTIVE:
            raise ValueError(f"只能对已生效处分标记家长沟通，当前状态: {sanction.status.value}")

        sanction.status = DisciplineStatus.PARENT_COMMUNICATED
        sanction.updated_at = get_local_now()

        # 🔔 通知多方（家长沟通完成）
        await DisciplineService._notify_on_discipline_event(
            db, sanction, "parent_communicated"
        )

        await db.commit()
        await db.refresh(sanction)
        logger.info(
            f"📞 家长沟通完成: student_id={sanction.student_id} "
            f"level={sanction.level.value} id={sanction.id}"
        )
        return sanction

    # ═══════════════════════════════════════════════════════════
    # 状态机: 结案 (PARENT_COMMUNICATED → CLOSED)
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    async def close_sanction(
        db: AsyncSession,
        sanction_id: int,
    ) -> DisciplineSanction | None:
        """
        结案归档 — PARENT_COMMUNICATED → CLOSED

        处分生命周期终结节点: 家长沟通完成后，
        年级组长/德育处确认结案，闭环结束。
        """
        sanction = await _query_by_id(db, sanction_id)
        if not sanction:
            return None
        if sanction.status != DisciplineStatus.PARENT_COMMUNICATED:
            raise ValueError(f"只能对「家长已沟通」处分结案，当前状态: {sanction.status.value}")

        sanction.status = DisciplineStatus.CLOSED
        sanction.updated_at = get_local_now()

        # 🔔 通知多方（结案）
        await DisciplineService._notify_on_discipline_event(
            db, sanction, "closed"
        )

        await db.commit()
        await db.refresh(sanction)
        logger.info(
            f"✅ 处分已结案: student_id={sanction.student_id} "
            f"level={sanction.level.value} id={sanction.id}"
        )
        return sanction

    # ═══════════════════════════════════════════════════════════
    # 违纪一键升级: behavior → discipline 裂变
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    async def check_escalation(db: AsyncSession, student_id: int) -> dict:
        """
        评估学生是否需要从违纪升级为处分

        逻辑:
          1. 汇总学生所有活跃违纪的总扣分
          2. 根据阈值建议处分等级
          3. 检查是否已有重复的 PENDING/ACTIVE 处分（幂等）
        """
        # 查询该学生活跃违纪统计
        from modules.behavior.models import DisciplineRecord as BehaviorRecord

        result = await db.execute(
            select(
                func.sum(BehaviorRecord.points).label("total_points"),
                func.count(BehaviorRecord.id).label("behavior_count"),
            ).where(
                BehaviorRecord.student_id == student_id,
                BehaviorRecord.status == "active",
            )
        )
        row = result.one()
        total_points = int(row.total_points or 0)
        behavior_count = int(row.behavior_count or 0)

        # 查询学生信息
        student = await db.scalar(select(Student).where(Student.id == student_id))
        student_name = student.name if student else None

        # 已有处分数
        existing_count = await db.scalar(
            select(func.count())
            .select_from(DisciplineSanction)
            .where(
                DisciplineSanction.student_id == student_id,
                DisciplineSanction.status.in_(
                    [
                        DisciplineStatus.PENDING,
                        DisciplineStatus.GRADE_LEADER_APPROVED,
                        DisciplineStatus.ACTIVE,
                    ]
                ),
            )
        )
        existing_count = int(existing_count or 0)

        # 建议处分等级
        suggested = None
        suggested_reason = None
        if total_points >= 50:
            suggested = DisciplineLevel.PROBATION.value
            suggested_reason = f"累计违纪扣分 {total_points} 分，建议给予留校察看处分"
        elif total_points >= 30:
            suggested = DisciplineLevel.DEMERIT.value
            suggested_reason = f"累计违纪扣分 {total_points} 分，建议给予记过处分"
        elif total_points >= 20:
            suggested = DisciplineLevel.SERIOUS_WARNING.value
            suggested_reason = f"累计违纪扣分 {total_points} 分，建议给予严重警告处分"
        elif total_points >= 10:
            suggested = DisciplineLevel.WARNING.value
            suggested_reason = f"累计违纪扣分 {total_points} 分，建议给予警告处分"

        can_escalate = (
            suggested is not None
            and total_points >= 10
            and existing_count == 0  # 幂等守卫：已有未处理的处分时不再生成
        )

        return {
            "student_id": student_id,
            "student_name": student_name,
            "total_points": total_points,
            "active_behavior_count": behavior_count,
            "suggested_level": suggested,
            "suggested_reason": suggested_reason,
            "existing_sanctions": existing_count,
            "can_escalate": can_escalate,
        }

    @staticmethod
    async def escalate_to_sanction(
        db: AsyncSession,
        student_id: int,
        created_by: int,
    ) -> DisciplineSanction | None:
        """
        一键升级：违纪 → 处分草案

        由班主任在违纪列表中触发的「升级为处分」按钮调用。
        自动创建 PENDING 状态处分，关联累计违纪中扣分最大的那条作为溯源。
        """
        # 先评估
        assessment = await DisciplineService.check_escalation(db, student_id)
        if not assessment["can_escalate"]:
            raise ValueError(
                f"当前不满足升级条件: total_points={assessment['total_points']}, "
                f"existing_sanctions={assessment['existing_sanctions']}"
            )

        student = await db.scalar(select(Student).where(Student.id == student_id))
        if not student:
            raise ValueError(f"学生不存在: id={student_id}")

        # 找到扣分最多的那条违纪记录作为溯源
        from modules.behavior.models import DisciplineRecord as BehaviorRecord

        top_behavior = await db.scalar(
            select(BehaviorRecord)
            .where(
                BehaviorRecord.student_id == student_id,
                BehaviorRecord.status == "active",
            )
            .order_by(BehaviorRecord.points.desc())
            .limit(1)
        )

        sanction = DisciplineSanction(
            school_id=student.school_id,
            student_id=student.id,
            class_id=student.class_id,
            grade_id=student.grade_id,
            level=DisciplineLevel(assessment["suggested_level"]),
            status=DisciplineStatus.PENDING,
            reason=assessment["suggested_reason"],
            behavior_record_id=top_behavior.id if top_behavior else None,
            punish_date=date.today(),
            creator_id=created_by,
        )
        db.add(sanction)
        await db.commit()
        # 🔔 违纪自动升级 → 通知年级组长
        await DisciplineService._notify_on_discipline_event(
            db, sanction, "pending", sender_id=created_by
        )
        await db.commit()

        sanction = await DisciplineService._reload_with_relations(db, sanction.id)
        logger.info(
            f"🔺 违纪自动升级为处分: student_id={student_id} "
            f"level={assessment['suggested_level']} id={sanction.id}"
        )
        return sanction

    # ═══════════════════════════════════════════════════════════
    # Phase 2: 30天滑窗自动化引擎 — behavior → discipline 熔焊
    # ═══════════════════════════════════════════════════════════

    # 滑窗参数
    ESCALATION_WINDOW_DAYS = 30  # 滑窗: 过去30天
    ESCALATION_SERIOUS_THRESHOLD = 3  # 阈值: ≥3次严重违纪

    @staticmethod
    async def detect_escalation_trigger(
        db: AsyncSession,
        student_id: int,
        school_id: int,
    ) -> dict:
        """
        30天滑窗规则判定器

        SQL 直查 behavior_records 表中:
          school_id == school_id AND type == "serious"
          AND incident_date >= 30天前 AND status == "active"

        W3-BE-RBAC-002 修复 R2-b: school_id 为必填参数，服务层强制租户过滤，
        杜绝跨租户 student_id 直读外校违纪明细。

        若 ≥3 次严重违纪，返回触发信号 + 铁证快照。
        幂等守卫: 检查是否已存在相同原因 DRAFT_PENDING/PENDING/ACTIVE 处分。

        Returns:
          {
            "triggered": bool,
            "serious_count": int,
            "window_start": str,       # 滑窗起始日期
            "window_end": str,         # 滑窗结束日期
            "evidence": [...],         # 铁证快照（最近3次严重违纪）
            "existing_draft_count": int,  # 已有草稿数（幂等守卫）
            "blocked_reason": str|None,
          }
        """
        from modules.behavior.models import DisciplineRecord as BehaviorRecord

        window_start = date.today() - timedelta(days=DisciplineService.ESCALATION_WINDOW_DAYS)
        window_end = date.today()

        # 查询30天内严重违纪记录
        serious_records = await db.execute(
            select(
                BehaviorRecord.id,
                BehaviorRecord.incident_date,
                BehaviorRecord.description,
                BehaviorRecord.points,
                BehaviorRecord.category,
            )
            .where(
                BehaviorRecord.school_id == school_id,
                BehaviorRecord.student_id == student_id,
                BehaviorRecord.type == "serious",
                BehaviorRecord.status == "active",
                BehaviorRecord.incident_date >= window_start,
                BehaviorRecord.incident_date <= window_end,
            )
            .order_by(BehaviorRecord.incident_date.desc())
        )
        evidence_rows = serious_records.all()
        serious_count = len(evidence_rows)

        # 构建铁证快照（取最近 3 次）
        evidence = []
        for row in evidence_rows[:3]:
            evidence.append(
                {
                    "behavior_id": int(row[0]),
                    "incident_date": row[1].isoformat() if row[1] else None,
                    "description": row[2],
                    "points": int(row[3] or 0),
                    "category": row[4],
                }
            )

        triggered = serious_count >= DisciplineService.ESCALATION_SERIOUS_THRESHOLD
        blocked_reason = None

        if triggered:
            # 幂等守卫: 检查是否已存在 DRAFT_PENDING/PENDING/ACTIVE 的"系统自动"处分
            existing = await db.scalar(
                select(func.count())
                .select_from(DisciplineSanction)
                .where(
                    DisciplineSanction.school_id == school_id,
                    DisciplineSanction.student_id == student_id,
                    DisciplineSanction.status.in_(
                        [
                            DisciplineStatus.DRAFT_PENDING,
                            DisciplineStatus.PENDING,
                            DisciplineStatus.GRADE_LEADER_APPROVED,
                            DisciplineStatus.ACTIVE,
                        ]
                    ),
                    DisciplineSanction.auto_generated == True,  # noqa: E712
                )
            )
            existing_count = int(existing or 0)
            if existing_count > 0:
                triggered = False
                blocked_reason = (
                    f"该学生已有 {existing_count} 条自动生成的处分记录(DRAFT_PENDING/PENDING/GL_APPROVED/ACTIVE)，"
                    f"无需重复生成"
                )
        else:
            existing_count = 0
            if serious_count == 0:
                blocked_reason = "过去30天内无严重违纪记录"
            elif serious_count < DisciplineService.ESCALATION_SERIOUS_THRESHOLD:
                blocked_reason = (
                    f"过去30天内严重违纪 {serious_count} 次，"
                    f"未达到 {DisciplineService.ESCALATION_SERIOUS_THRESHOLD} 次阈值"
                )

        result = {
            "triggered": triggered,
            "serious_count": serious_count,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "evidence": evidence,
            "existing_draft_count": existing_count if triggered else 0,
            "blocked_reason": blocked_reason,
        }
        return result

    @staticmethod
    async def create_escalation_draft(
        db: AsyncSession,
        student_id: int,
        evidence: list,
        db_session_for_commit: bool = True,
    ) -> DisciplineSanction | None:
        """
        生成处分草稿 → DRAFT_PENDING 状态

        不直接生成生效处分，而是写入 DRAFT_PENDING 草稿。
        班主任可通过 POST /drafts/{id}/submit 一键提交为 PENDING。

        Args:
            db: 数据库会话
            student_id: 学生 ID
            evidence: detect_escalation_trigger() 返回的 evidence 列表
            db_session_for_commit: 是否由本方法 commit（Hook 注入场景中为 False）

        Returns:
            生成的草稿记录，或 None（幂等拦截）
        """
        # 查询学生信息
        student = await db.scalar(select(Student).where(Student.id == student_id))
        if not student:
            raise ValueError(f"学生不存在: id={student_id}")

        # 二次幂等检查
        existing = await db.scalar(
            select(func.count())
            .select_from(DisciplineSanction)
            .where(
                DisciplineSanction.student_id == student_id,
                DisciplineSanction.auto_generated == True,  # noqa: E712
                DisciplineSanction.status.in_(
                    [
                        DisciplineStatus.DRAFT_PENDING,
                        DisciplineStatus.PENDING,
                        DisciplineStatus.GRADE_LEADER_APPROVED,
                        DisciplineStatus.ACTIVE,
                    ]
                ),
            )
        )
        if existing and existing > 0:
            logger.info(f"⏭️ 草稿已存在(幂等跳过): student_id={student_id}")
            return None

        # 处分等级: 3次严重违纪 → 警告
        suggested_level = DisciplineLevel.WARNING
        total_points = sum(e.get("points", 0) for e in evidence)

        # 构造事由
        incident_dates = [e["incident_date"] for e in evidence if e.get("incident_date")]
        date_summary = "、".join(incident_dates) if incident_dates else "近期多次"

        reason = (
            f"[30天滑窗自动触发] 学生 {student.name} 在过去30天内累计 {len(evidence)} 次严重违纪"
            f" ({date_summary})，累计扣分 {total_points} 分，"
            f"系统自动生成「{LEVEL_LABELS[suggested_level]}」处分草稿，请班主任确认。"
        )

        # SQLAlchemy JSON 类型自动序列化，直接传入 list
        draft = DisciplineSanction(
            school_id=student.school_id,
            student_id=student.id,
            class_id=student.class_id,
            grade_id=student.grade_id,
            level=suggested_level,
            status=DisciplineStatus.DRAFT_PENDING,
            reason=reason,
            evidence_snapshot=evidence,  # JSON 列自动序列化
            auto_generated=True,
            behavior_record_id=evidence[0].get("behavior_id") if evidence else None,
            punish_date=date.today(),
            creator_id=None,  # 系统自动生成，无 creator（FK 可为空）
        )
        db.add(draft)

        if db_session_for_commit:
            await db.commit()
            draft = await DisciplineService._reload_with_relations(db, draft.id)

        logger.warning(
            f"🤖 自动生成处分草稿: student_id={student_id} "
            f"serious_count={len(evidence)} level={suggested_level.value} "
            f"id={draft.id}"
        )
        return draft

    @staticmethod
    async def list_drafts(
        db: AsyncSession,
        school_id: int,
        class_id: int | None = None,
        grade_id: int | None = None,
        student_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[DisciplineSanction], int]:
        """
        查询处分草稿列表 — DRAFT_PENDING 状态

        按角色自动过滤:
          - 班主任: 只看自己班级
          - 年级组长: 看全年级
          - 德育处: 看全校
        """
        conditions = [
            DisciplineSanction.school_id == school_id,
            DisciplineSanction.status == DisciplineStatus.DRAFT_PENDING,
        ]
        if class_id:
            conditions.append(DisciplineSanction.class_id == class_id)
        if grade_id:
            conditions.append(DisciplineSanction.grade_id == grade_id)
        if student_id:
            conditions.append(DisciplineSanction.student_id == student_id)

        cnt = await db.scalar(
            select(func.count()).select_from(DisciplineSanction).where(*conditions)
        )
        total = int(cnt or 0)

        stmt = (
            select(DisciplineSanction)
            .options(
                selectinload(DisciplineSanction.student).selectinload(Student.class_),
                selectinload(DisciplineSanction.class_),
                selectinload(DisciplineSanction.grade),
            )
            .where(*conditions)
            .order_by(DisciplineSanction.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    @staticmethod
    async def submit_draft(
        db: AsyncSession,
        draft_id: int,
        confirm_reason: str | None = None,
        submitter_id: int | None = None,
    ) -> DisciplineSanction | None:
        """
        班主任一键提交草稿: DRAFT_PENDING → PENDING

        草稿瞬间转为正式 PENDING 状态，进入德育处行政审批流程。
        """
        draft = await _query_by_id(db, draft_id)
        if not draft:
            return None
        if draft.status != DisciplineStatus.DRAFT_PENDING:
            raise ValueError(f"只能提交草稿状态的记录，当前状态: {draft.status.value}")

        now = get_local_now()

        # 状态转换
        draft.status = DisciplineStatus.PENDING
        draft.updated_at = now

        # 班主任补充意见追加到事由
        if confirm_reason:
            draft.reason = f"{draft.reason}\n\n【班主任补充意见】{confirm_reason}"

        # 记录提报人
        if submitter_id:
            draft.creator_id = submitter_id

        await db.commit()
        # 🔔 草稿提交 → 通知年级组长
        await DisciplineService._notify_on_discipline_event(
            db, draft, "pending", sender_id=submitter_id
        )
        await db.commit()
        await db.refresh(draft)

        logger.info(
            f"📤 草稿已提交→PENDING: student_id={draft.student_id} "
            f"id={draft.id} submitter_id={submitter_id}"
        )
        return draft

    @staticmethod
    async def discard_draft(db: AsyncSession, draft_id: int) -> bool:
        """废弃草稿 — 物理删除 DRAFT_PENDING 记录"""
        draft = await _query_by_id(db, draft_id)
        if not draft:
            return False
        if draft.status != DisciplineStatus.DRAFT_PENDING:
            raise ValueError(f"只能废弃草稿状态的记录，当前状态: {draft.status.value}")

        # P1 修复: 先清理关联的申诉记录（FK 约束 sanction_appeals.sanction_id）
        await db.execute(delete(SanctionAppeal).where(SanctionAppeal.sanction_id == draft_id))

        await db.delete(draft)
        await db.commit()
        logger.info(f"🗑️ 草稿已废弃: id={draft_id} student_id={draft.student_id}")
        return True

    # ═══════════════════════════════════════════════════════════
    # 统计
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    async def get_stats(
        db: AsyncSession,
        school_id: int,
        grade_id: int | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict:
        """处分统计概览"""
        conditions = [DisciplineSanction.school_id == school_id]
        if grade_id:
            conditions.append(DisciplineSanction.grade_id == grade_id)
        if start_date:
            conditions.append(DisciplineSanction.punish_date >= start_date)
        if end_date:
            conditions.append(DisciplineSanction.punish_date <= end_date)

        total = int(
            await db.scalar(select(func.count()).select_from(DisciplineSanction).where(*conditions))
            or 0
        )

        # 按等级分组
        level_rows = await db.execute(
            select(
                DisciplineSanction.level,
                func.count(DisciplineSanction.id),
            )
            .where(*conditions)
            .group_by(DisciplineSanction.level)
        )
        by_level = {row[0].value: row[1] for row in level_rows.all()}

        # 按状态分组
        status_rows = await db.execute(
            select(
                DisciplineSanction.status,
                func.count(DisciplineSanction.id),
            )
            .where(*conditions)
            .group_by(DisciplineSanction.status)
        )
        by_status = {row[0].value: row[1] for row in status_rows.all()}

        # 按班级分组
        class_rows = await db.execute(
            select(
                DisciplineSanction.class_id,
                Class.name,
                func.count(DisciplineSanction.id),
            )
            .join(Class, DisciplineSanction.class_id == Class.id)
            .where(*conditions)
            .group_by(DisciplineSanction.class_id, Class.name)
        )
        by_class = {row[1]: row[2] for row in class_rows.all()}

        # ACTIVE 处分总数
        active_conds = list(conditions) + [
            DisciplineSanction.status == DisciplineStatus.ACTIVE,
        ]
        active_count = int(
            await db.scalar(
                select(func.count()).select_from(DisciplineSanction).where(*active_conds)
            )
            or 0
        )

        # 一票否决学生数 (PROBATION 且 ACTIVE)
        veto_conds = list(conditions) + [
            DisciplineSanction.status == DisciplineStatus.ACTIVE,
            DisciplineSanction.level.in_(list(VETO_LEVELS)),
        ]
        veto_count = int(
            await db.scalar(
                select(func.count(func.distinct(DisciplineSanction.student_id)))
                .select_from(DisciplineSanction)
                .where(*veto_conds)
            )
            or 0
        )

        return {
            "total": total,
            "by_level": by_level,
            "by_status": by_status,
            "by_class": by_class,
            "active_count": active_count,
            "veto_count": veto_count,
        }

    # ═══════════════════════════════════════════════════════════
    # Phase 4: 家校申诉 Webhook — 原子状态机
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    async def create_appeal_from_webhook(
        db: AsyncSession,
        school_id: int,
        data: dict,
    ) -> dict:
        """
        Webhook 接收外部系统申诉 → 创建待复核申诉记录

        原子状态机保障:
          1. 幂等检查: idempotency_key 唯一约束防重复提交
          2. 处分状态校验: 仅 ACTIVE 状态的处分可被申诉
          3. 唯一性守卫: 同一处分不可有多个 PENDING 申诉

        Returns:
          {"appeal": SanctionAppeal, "created": bool}
          created=False 表示幂等返回已存在记录
        """
        from .models import AppealStatus, SanctionAppeal

        idem_key = data["idempotency_key"]
        sanction_id = data["sanction_id"]

        # ── 幂等检查 ──
        existing = await db.scalar(
            select(SanctionAppeal).where(
                SanctionAppeal.idempotency_key == idem_key,
            )
        )
        if existing:
            logger.info(f"⏭️ 申诉幂等拦截: idempotency_key={idem_key}")
            return {"appeal": existing, "created": False}

        # ── 处分状态校验 ──
        sanction = await _query_by_id(db, sanction_id)
        if not sanction:
            raise ValueError(f"处分记录不存在: id={sanction_id}")
        if sanction.status != DisciplineStatus.ACTIVE:
            raise ValueError(f"仅生效中的处分可申诉，当前状态: {sanction.status.value}")

        # ── 唯一性守卫: 同一处分不可有多个 PENDING 申诉 ──
        pending_appeal = await db.scalar(
            select(SanctionAppeal).where(
                SanctionAppeal.sanction_id == sanction_id,
                SanctionAppeal.status == AppealStatus.PENDING,
            )
        )
        if pending_appeal:
            raise ValueError(
                f"该处分已有待复核的申诉 (id={pending_appeal.id})，请等待复核结果后再提交"
            )

        # ── 创建申诉 ──
        appeal = SanctionAppeal(
            school_id=school_id,
            sanction_id=sanction_id,
            applicant_name=data["applicant_name"],
            applicant_phone=data.get("applicant_phone"),
            reason=data["reason"],
            idempotency_key=idem_key,
            status=AppealStatus.PENDING,
        )
        db.add(appeal)
        await db.flush()

        # 🔔 通知德育处管理员
        await DisciplineService._notify_on_appeal_event(db, appeal, sanction, "created")
        await db.commit()
        await db.refresh(appeal)

        logger.info(
            f"📩 家校申诉已接收: sanction_id={sanction_id} "
            f"applicant={data['applicant_name']} appeal_id={appeal.id}"
        )
        return {"appeal": appeal, "created": True}

    @staticmethod
    async def review_appeal(
        db: AsyncSession,
        appeal_id: int,
        action: str,
        reviewer_id: int,
        comment: str | None = None,
    ) -> dict:
        """
        德育处复核申诉 → 原子状态机

        ACCEPTED (申诉通过):
          1. 申诉状态 → ACCEPTED
          2. 自动撤销原处分 ACTIVE → REVOKED
          3. 撤销原因注入: "家长申诉通过 — {申诉事由摘要}"
          4. 通知班主任 + 年级组长 + 申诉人（邮件/SMS由外部系统处理）

        REJECTED (申诉驳回):
          1. 申诉状态 → REJECTED
          2. 原处分不受影响（维持 ACTIVE）
          3. 通知班主任 + 申诉人

        Returns:
          {"appeal": SanctionAppeal, "sanction": DisciplineSanction|None}
        """
        from .models import AppealStatus, SanctionAppeal

        if action not in ("ACCEPTED", "REJECTED"):
            raise ValueError(f"无效复核动作: {action}，有效值: ACCEPTED / REJECTED")

        appeal = await db.scalar(select(SanctionAppeal).where(SanctionAppeal.id == appeal_id))
        if not appeal:
            raise ValueError(f"申诉记录不存在: id={appeal_id}")
        if appeal.status != AppealStatus.PENDING:
            raise ValueError(f"仅「待复核」的申诉可操作，当前状态: {appeal.status.value}")

        now = get_local_now()

        # ── 更新申诉状态 ──
        appeal.status = AppealStatus(action)
        appeal.reviewer_id = reviewer_id
        appeal.review_comment = comment or None
        appeal.reviewed_at = now
        appeal.updated_at = now

        sanction = None
        event_type = None

        if action == "ACCEPTED":
            # ── 申诉通过 → 自动撤销处分 ──
            sanction = await _query_by_id(db, appeal.sanction_id)
            if sanction and sanction.status == DisciplineStatus.ACTIVE:
                revoke_reason = (
                    f"家长申诉通过 — {appeal.reason[:80]}{'...' if len(appeal.reason) > 80 else ''}"
                )
                sanction.status = DisciplineStatus.REVOKED
                sanction.revoke_reason = revoke_reason
                sanction.revoke_date = date.today()
                sanction.updated_at = now

                # 解除一票否决
                await DisciplineService._lift_veto_if_single(db, sanction)

                # ═══ PolicyEngine Hook-4: 申诉通过→处分撤销→通道A 100%回血 ═══
                await DisciplineService._apply_revocation_recovery(db, sanction)

                event_type = "accepted"
                logger.info(
                    f"✅ 申诉通过→处分撤销: sanction_id={sanction.id} "
                    f"appeal_id={appeal.id} student_id={sanction.student_id}"
                )
        else:
            # ── 申诉驳回 ──
            event_type = "rejected"
            sanction = await _query_by_id(db, appeal.sanction_id)  # 加载处分记录用于通知
            logger.info(f"❌ 申诉驳回: appeal_id={appeal.id} sanction_id={appeal.sanction_id}")

        # 🔔 通知
        if sanction:
            await DisciplineService._notify_on_appeal_event(
                db, appeal, sanction, event_type, reviewer_id=reviewer_id
            )

        await db.commit()
        await db.refresh(appeal)

        return {"appeal": appeal, "sanction": sanction}

    @staticmethod
    async def list_appeals(
        db: AsyncSession,
        school_id: int,
        sanction_id: int | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list, int]:
        """分页查询申诉列表"""
        from .models import AppealStatus, SanctionAppeal

        conditions = [SanctionAppeal.school_id == school_id]
        if sanction_id:
            conditions.append(SanctionAppeal.sanction_id == sanction_id)
        if status:
            try:
                conditions.append(SanctionAppeal.status == AppealStatus(status))
            except ValueError:
                pass

        cnt = await db.scalar(select(func.count()).select_from(SanctionAppeal).where(*conditions))
        total = int(cnt or 0)

        stmt = (
            select(SanctionAppeal)
            .options(
                selectinload(SanctionAppeal.sanction),
                selectinload(SanctionAppeal.reviewer),
            )
            .where(*conditions)
            .order_by(SanctionAppeal.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    @staticmethod
    async def get_appeal(db: AsyncSession, appeal_id: int):
        """查询单条申诉详情"""
        from .models import SanctionAppeal

        result = await db.execute(
            select(SanctionAppeal)
            .options(
                selectinload(SanctionAppeal.sanction),
                selectinload(SanctionAppeal.reviewer),
            )
            .where(SanctionAppeal.id == appeal_id)
        )
        return result.scalar_one_or_none()

    # ═══════════════════════════════════════════════════════════
    # Phase 4: 申诉通知 Hook
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    async def _notify_on_appeal_event(
        db: AsyncSession,
        appeal,  # SanctionAppeal
        sanction,  # DisciplineSanction
        event: str,
        reviewer_id: int | None = None,
    ):
        """
        申诉事件通知 Hook

        event 取值:
          created   → 新申诉已提交（→ 德育处管理员）
          accepted  → 申诉通过、处分已撤销（→ 班主任 + 年级组长）
          rejected  → 申诉驳回（→ 班主任）
        """
        from modules.notifications.services import NotificationService

        # 获取学生姓名
        student_result = await db.execute(select(Student).where(Student.id == sanction.student_id))
        student = student_result.scalar_one_or_none()
        student_name = student.name if student else f"学生#{sanction.student_id}"

        level_label = LEVEL_LABELS.get(sanction.level, sanction.level.value)
        school_id = sanction.school_id
        entity_id = appeal.id or 0

        if event == "created":
            notify_role = UserRole.MS_ADMIN
            title = f"家校申诉待复核 — {student_name}"
            body = (
                f"家长 {appeal.applicant_name} 对 {student_name} 的"
                f"{level_label}处分提出申诉。"
                f"申诉事由: {appeal.reason[:50]}"
                f"{'...' if len(appeal.reason) > 50 else ''}"
            )
            notify_type = "appeal_created"

        elif event == "accepted":
            notify_users = []
            if sanction.creator_id:
                notify_users.append(sanction.creator_id)
            if sanction.grade_leader_id:
                notify_users.append(sanction.grade_leader_id)
            title = f"申诉通过、处分已撤销 — {student_name}"
            body = (
                f"家长 {appeal.applicant_name} 的申诉已被接受。"
                f"{student_name} 的{level_label}处分已自动撤销。"
            )
            notify_type = "appeal_accepted"

        elif event == "rejected":
            notify_users = [sanction.creator_id] if sanction.creator_id else []
            title = f"申诉已驳回 — {student_name}"
            body = (
                f"家长 {appeal.applicant_name} 关于 {student_name} "
                f"{level_label}处分的申诉已被驳回。"
                f"驳回意见: {appeal.review_comment or '无'}"
            )
            notify_type = "appeal_rejected"

        else:
            logger.warning(f"未知申诉通知事件: {event}")
            return

        try:
            if event == "created":
                await NotificationService.notify_by_role(
                    db,
                    school_id,
                    notify_role,
                    type=notify_type,
                    title=title,
                    body=body,
                    sender_id=reviewer_id,
                    entity_type="sanction_appeal",
                    entity_id=entity_id,
                )
            if event in ("accepted", "rejected") and notify_users:
                await NotificationService.notify_users(
                    db,
                    notify_users,
                    type=notify_type,
                    title=title,
                    body=body,
                    sender_id=reviewer_id,
                    entity_type="sanction_appeal",
                    entity_id=entity_id,
                    school_id=school_id,
                )
        except Exception as e:
            logger.error(
                f"申诉通知推送失败 [{event} appeal_id={entity_id}]: {e}",
                exc_info=True,
            )

    # ═══════════════════════════════════════════════════════════
    # 内部辅助
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    async def _reload_with_relations(db: AsyncSession, sanction_id: int) -> DisciplineSanction:
        """重查询处分记录并加载所有关系"""
        result = await db.execute(
            select(DisciplineSanction)
            .options(
                selectinload(DisciplineSanction.student).selectinload(Student.class_),
                selectinload(DisciplineSanction.creator),
                selectinload(DisciplineSanction.approver),
                selectinload(DisciplineSanction.grade_leader),
                selectinload(DisciplineSanction.class_),
                selectinload(DisciplineSanction.grade),
            )
            .where(DisciplineSanction.id == sanction_id)
        )
        return result.scalar_one()

    @staticmethod
    async def _check_auto_escalation(
        db: AsyncSession,
        student: Student,
        new_sanction: DisciplineSanction,
    ):
        """
        检查单学期 ACTIVE 处分次数 → 自动升级到更高等级

        当学生单学期 ACTIVE/PENDING 处分数达到阈值，
        自动创建一条更高级别的处分记录。幂等：相同等级不重复生成。
        """
        # 统计学生单学期现有处分数（PENDING + GL_APPROVED + ACTIVE）
        existing_count = await db.scalar(
            select(func.count())
            .select_from(DisciplineSanction)
            .where(
                DisciplineSanction.student_id == student.id,
                DisciplineSanction.status.in_(
                    [
                        DisciplineStatus.PENDING,
                        DisciplineStatus.GRADE_LEADER_APPROVED,
                        DisciplineStatus.ACTIVE,
                    ]
                ),
            )
        )
        existing_count = int(existing_count or 0)

        for threshold, upgrade_level in sorted(AUTO_ESCALATION_MAP.items()):
            if existing_count >= threshold:
                # 幂等检查
                has_upgrade = await db.scalar(
                    select(func.count())
                    .select_from(DisciplineSanction)
                    .where(
                        DisciplineSanction.student_id == student.id,
                        DisciplineSanction.level == upgrade_level,
                        DisciplineSanction.status.in_(
                            [
                                DisciplineStatus.PENDING,
                                DisciplineStatus.GRADE_LEADER_APPROVED,
                                DisciplineStatus.ACTIVE,
                            ]
                        ),
                    )
                )
                if has_upgrade and has_upgrade > 0:
                    continue

                escalate = DisciplineSanction(
                    school_id=student.school_id,
                    student_id=student.id,
                    class_id=student.class_id,
                    grade_id=student.grade_id,
                    level=upgrade_level,
                    status=DisciplineStatus.PENDING,
                    reason=(
                        f"[自动升级] 学生 {student.name} 本学期已累计 {existing_count} 次处分，"
                        f"自动升级为「{LEVEL_LABELS[upgrade_level]}」"
                    ),
                    behavior_record_id=new_sanction.behavior_record_id,
                    punish_date=date.today(),
                    creator_id=new_sanction.creator_id,
                )
                db.add(escalate)
                logger.warning(
                    f"⚠️ 处分自动升级: student_id={student.id} "
                    f"existing={existing_count} → {upgrade_level.value}"
                )
                break

    @staticmethod
    async def _notify_on_discipline_event(
        db: AsyncSession,
        sanction: DisciplineSanction,
        event: str,
        sender_id: int | None = None,
        extra: str | None = None,
    ):
        """
        通知引擎 Hook — 处分状态变更时向相关用户推送通知

        event 取值:
          pending       → 新处分待初审（→ 年级组长）
          gl_approved   → 年级组长初审通过（→ 德育处）
          activated     → 处分正式生效（→ 班主任）
          rejected_by_gl → 年级组长驳回（→ 班主任）
          rejected_by_ms → 德育处驳回（→ 班主任 + 年级组长）
          revoked       → 处分已撤销（→ 班主任 + 年级组长 + 德育处）
        """
        from modules.notifications.services import NotificationService

        # 获取学生姓名
        student_result = await db.execute(select(Student).where(Student.id == sanction.student_id))
        student = student_result.scalar_one_or_none()
        student_name = student.name if student else f"学生#{sanction.student_id}"

        level_label = LEVEL_LABELS.get(sanction.level, sanction.level.value)
        school_id = sanction.school_id
        entity_id = sanction.id or 0

        # 通知类型映射
        notify_type_map = {
            "pending": "discipline_pending",
            "gl_approved": "discipline_gl_approved",
            "activated": "discipline_activated",
            "rejected_by_gl": "discipline_rejected",
            "rejected_by_ms": "discipline_rejected",
            "revoked": "discipline_revoked",
        }
        notify_type = notify_type_map.get(event, f"discipline_{event}")

        # ── 按事件类型确定收件人、标题、正文 ──
        notify_role = None
        notify_users = None

        if event == "pending":
            notify_role = UserRole.GRADE_LEADER
            title = f"新处分待审批 — {student_name}"
            body = (
                f"学生 {student_name} 因「{sanction.reason[:40]}"
                f"{'...' if len(sanction.reason) > 40 else ''}」"
                f"被提报{level_label}处分，请及时审核。"
            )
            sender = sender_id or sanction.creator_id

        elif event == "gl_approved":
            notify_role = UserRole.MS_ADMIN
            title = f"处分待终审 — {student_name}"
            body = f"年级组长已初审通过 {student_name} 的{level_label}处分，请进行终审。"
            sender = sender_id

        elif event == "activated":
            notify_users = [sanction.creator_id] if sanction.creator_id else []
            title = f"处分已生效 — {student_name}"
            body = f"学生 {student_name} 的{level_label}处分已正式生效。{extra or ''}"
            sender = sender_id

        elif event == "rejected_by_gl":
            notify_users = [sanction.creator_id] if sanction.creator_id else []
            title = f"处分申请被驳回 — {student_name}"
            body = f"学生 {student_name} 的{level_label}处分申请被年级组长驳回。驳回意见: {extra or '无'}"
            sender = sender_id

        elif event == "rejected_by_ms":
            notify_users = []
            if sanction.creator_id:
                notify_users.append(sanction.creator_id)
            if sanction.grade_leader_id:
                notify_users.append(sanction.grade_leader_id)
            title = f"处分申请被驳回 — {student_name}"
            body = f"学生 {student_name} 的{level_label}处分申请被德育处驳回。驳回意见: {extra or '无'}"
            sender = sender_id

        elif event == "revoked":
            notify_users = []
            if sanction.creator_id:
                notify_users.append(sanction.creator_id)
            if sanction.grade_leader_id:
                notify_users.append(sanction.grade_leader_id)
            if sanction.approver_id:
                notify_users.append(sanction.approver_id)
            title = f"处分已撤销 — {student_name}"
            body = f"学生 {student_name} 的{level_label}处分已被撤销。撤销原因: {extra or '无'}"
            sender = sender_id

        else:
            logger.warning(f"未知通知事件类型: {event}")
            return

        # ── 执行推送 ──
        try:
            if notify_role:
                await NotificationService.notify_by_role(
                    db,
                    school_id,
                    notify_role,
                    type=notify_type,
                    title=title,
                    body=body,
                    sender_id=sender,
                    entity_type="discipline_sanction",
                    entity_id=entity_id,
                )
            if notify_users:
                await NotificationService.notify_users(
                    db,
                    notify_users,
                    type=notify_type,
                    title=title,
                    body=body,
                    sender_id=sender,
                    entity_type="discipline_sanction",
                    entity_id=entity_id,
                    school_id=school_id,
                )
        except Exception as e:
            logger.error(f"通知推送失败 [{event} sanction_id={entity_id}]: {e}", exc_info=True)

    @staticmethod
    async def _apply_penalty(db: AsyncSession, sanction: DisciplineSanction):
        """
        ACTIVE 时联动评价引擎 + PolicyEngine 回血追踪初始化

        阶梯扣分模型:
          WARNING         → -5 分 (repairable, 7天观察期)
          SERIOUS_WARN    → -10 分 (repairable, 14天观察期)
          DEMERIT         → -20 分 (repairable, 30天观察期)
          PROBATION       → 一票否决 (non_repairable, 不回血)
          EXPULSION       → 开除 (permanent, 不回血)

        策略: 一次性扣分，撤销后不回溯
        PolicyEngine Hook-3: 初始化 RecoveryState 回血追踪
        """
        penalty = LEVEL_PENALTY_MAP.get(sanction.level)
        is_veto = sanction.level in VETO_LEVELS

        # ── 原有日志逻辑 ──
        if penalty is not None:
            logger.info(
                f"📉 处分扣分: student_id={sanction.student_id} "
                f"level={sanction.level.value} penalty={penalty} points"
            )
        elif is_veto:
            logger.warning(
                f"🚫 一票否决: student_id={sanction.student_id} "
                f"level={sanction.level.value} — 学期德育总评自动不合格"
            )

        # ═══ PolicyEngine Hook-3: 处分生效→回血追踪初始化 ═══
        try:
            from datetime import timedelta

            from modules.evaluation.models import RecoveryState
            from modules.policy_engine import get_engine

            # 处分等级 → PolicyEngine severity 名称（与 policy.yaml per_severity 对齐）
            _LEVEL_SEVERITY_MAP = {
                DisciplineLevel.WARNING: "warning",
                DisciplineLevel.SERIOUS_WARNING: "serious_warning",
                DisciplineLevel.DEMERIT: "demerit",
                DisciplineLevel.PROBATION: "probation",
                DisciplineLevel.EXPULSION: "expulsion",
            }

            # 处分等级 → 观察期天数（与 policy.yaml min_observation_days_override 对齐）
            _OBSERVATION_DAYS = {
                "warning": 7,
                "serious_warning": 14,
                "demerit": 30,
                "probation": 0,  # 不可回血，无观察期
                "expulsion": 0,  # 不可回血，无观察期
            }

            # 处分等级 → policy_tag（与 policy.yaml tag_on_apply 对齐）
            _LEVEL_POLICY_TAG = {
                DisciplineLevel.WARNING: "repairable",
                DisciplineLevel.SERIOUS_WARNING: "repairable",
                DisciplineLevel.DEMERIT: "repairable",
                DisciplineLevel.PROBATION: "non_repairable",
                DisciplineLevel.EXPULSION: "permanent",
            }

            severity = _LEVEL_SEVERITY_MAP.get(sanction.level, "serious_warning")
            policy_tag = _LEVEL_POLICY_TAG.get(sanction.level, "repairable")
            original_penalty = abs(penalty) if penalty is not None else 0.0
            obs_days = _OBSERVATION_DAYS.get(severity, 14)
            today = date.today()

            # 1. 创建 RecoveryState 追踪记录
            recovery = RecoveryState(
                school_id=sanction.school_id,
                student_id=sanction.student_id,
                source_type="discipline",
                source_id=sanction.id,
                severity=severity,
                original_penalty=original_penalty,
                recovered_amount=0.0,
                remaining_penalty=original_penalty,
                recovery_ratio=0.0,
                policy_tag=policy_tag,
                observation_start=today,
                observation_end=today + timedelta(days=obs_days) if obs_days > 0 else today,
                last_computed_at=get_local_now(),
                is_active=(policy_tag in ("repairable",)),
            )
            db.add(recovery)

            # 2. 如果有 PolicyEngine，记录分类信息
            engine = get_engine()
            if engine:
                behavior_type = f"discipline_{sanction.level.value.lower()}"
                classification = engine.classify(behavior_type)
                logger.info(
                    f"[PolicyEngine Hook-3] 处分→回血追踪初始化: "
                    f"student_id={sanction.student_id} sanction_id={sanction.id} "
                    f"level={sanction.level.value} severity={severity} "
                    f"policy_tag={policy_tag} obs_days={obs_days} "
                    f"original_penalty={original_penalty} "
                    f"pe_severity={classification.severity}"
                )
            else:
                logger.info(
                    f"[PolicyEngine Hook-3] 处分→回血追踪初始化(降级模式): "
                    f"student_id={sanction.student_id} sanction_id={sanction.id} "
                    f"policy_tag={policy_tag}"
                )

            await db.flush()

        except Exception as e:
            logger.error(
                f"[PolicyEngine Hook-3] 异常(已隔离，处分状态变更不受影响): "
                f"sanction_id={sanction.id} student_id={sanction.student_id} "
                f"error={e}",
                exc_info=True,
            )
            # 不 re-raise — Fail-Soft 铁律

    @staticmethod
    async def _apply_revocation_recovery(db: AsyncSession, sanction: DisciplineSanction):
        """
        PolicyEngine Hook-4: 处分撤销→通道A 100%回血

        撤销后:
          - RecoveryState 更新: recovered_amount = original_penalty, policy_tag = "recovered"
          - 关联 ScoreLog policy_tag 更新为 "recovered"
          - 实际分数恢复由 recalculate_snapshot() 自动处理（sanction 不再 ACTIVE）
        """
        try:
            from modules.evaluation.models import RecoveryState, ScoreLog

            # 1. 查询关联的 RecoveryState
            result = await db.execute(
                select(RecoveryState).where(
                    RecoveryState.source_type == "discipline",
                    RecoveryState.source_id == sanction.id,
                )
            )
            recovery = result.scalar_one_or_none()

            if recovery:
                recovery.recovered_amount = recovery.original_penalty
                recovery.remaining_penalty = 0.0
                recovery.recovery_ratio = 1.0 if recovery.original_penalty > 0 else 0.0
                recovery.policy_tag = "recovered"
                recovery.is_active = False
                recovery.last_computed_at = get_local_now()
                logger.info(
                    f"[PolicyEngine Hook-4] 处分撤销→通道A 100%回血: "
                    f"student_id={sanction.student_id} sanction_id={sanction.id} "
                    f"recovered={recovery.original_penalty} "
                    f"policy_tag=recovered"
                )
            else:
                logger.warning(
                    f"[PolicyEngine Hook-4] 未找到关联 RecoveryState: "
                    f"sanction_id={sanction.id} "
                    f"student_id={sanction.student_id} "
                    f"(可能为 Hook-3 之前创建的处分)"
                )

            # 2. 更新关联 ScoreLog 的 policy_tag → "recovered"
            log_result = await db.execute(
                select(ScoreLog).where(
                    ScoreLog.source_type == "discipline",
                    ScoreLog.source_id == sanction.id,
                )
            )
            logs = list(log_result.scalars().all())
            for log in logs:
                log.policy_tag = "recovered"
            if logs:
                logger.info(
                    f"[PolicyEngine Hook-4] 关联 ScoreLog policy_tag 更新: "
                    f"count={len(logs)} sanction_id={sanction.id}"
                )

            await db.flush()

        except Exception as e:
            logger.error(
                f"[PolicyEngine Hook-4] 异常(已隔离，处分撤销不受影响): "
                f"sanction_id={sanction.id} student_id={sanction.student_id} "
                f"error={e}",
                exc_info=True,
            )
            # 不 re-raise — Fail-Soft 铁律

    @staticmethod
    async def _lift_veto_if_single(db: AsyncSession, sanction: DisciplineSanction):
        """
        撤销一票否决标记

        如果此处分是唯一触发否决的来源，撤销后解除否决。
        如果学生还有其他 ACTIVE 的 PROBATION/EXPULSION 处分，则保留否决。
        """
        if sanction.level not in VETO_LEVELS:
            return

        others = await db.scalar(
            select(func.count())
            .select_from(DisciplineSanction)
            .where(
                DisciplineSanction.student_id == sanction.student_id,
                DisciplineSanction.id != sanction.id,
                DisciplineSanction.status == DisciplineStatus.ACTIVE,
                DisciplineSanction.level.in_(list(VETO_LEVELS)),
            )
        )
        if not others or others == 0:
            logger.info(
                f"✅ 一票否决解除: student_id={sanction.student_id} "
                f"唯一定罪处分 id={sanction.id} 已撤销"
            )


# ═══════════════════════════════════════════════════════════════
# 模块内辅助
# ═══════════════════════════════════════════════════════════════


async def _query_by_id(db: AsyncSession, sanction_id: int) -> DisciplineSanction | None:
    result = await db.execute(
        select(DisciplineSanction)
        .options(
            selectinload(DisciplineSanction.student).selectinload(Student.class_),
            selectinload(DisciplineSanction.creator),
            selectinload(DisciplineSanction.approver),
            selectinload(DisciplineSanction.grade_leader),
            selectinload(DisciplineSanction.class_),
            selectinload(DisciplineSanction.grade),
        )
        .where(DisciplineSanction.id == sanction_id)
    )
    return result.scalar_one_or_none()
