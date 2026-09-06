"""
tests/test_class_mgmt.py — 班级管理模块单元测试

覆盖：班级创建/学生分班/调班/班主任分配/统计等核心逻辑。

运行方式：
    cd backend
    python -m pytest tests/test_class_mgmt.py -v
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

# ── 测试班级创建 ──


class TestCreateClass:
    @pytest.mark.asyncio
    async def test_create_class_success(self):
        """成功创建班级"""
        from modules.class_mgmt.schemas import ClassCreate
        from modules.class_mgmt.services import ClassMgmtService

        db = MagicMock()
        grade = MagicMock()
        grade.school_id = 1
        db.get = AsyncMock(return_value=grade)

        # Mock 名称查重
        existing_mock = MagicMock()
        existing_mock.scalar_one_or_none = MagicMock(return_value=None)
        db.execute = AsyncMock(return_value=existing_mock)

        db.add = MagicMock()
        db.flush = AsyncMock()

        data = ClassCreate(name="2501", grade_id=1)
        cls = await ClassMgmtService.create_class(db, 1, data)

        assert cls.name == "2501"
        assert cls.is_active is True
        assert cls.student_count == 0

    @pytest.mark.asyncio
    async def test_create_class_duplicate_name(self):
        """班级名重复"""
        from modules.class_mgmt.schemas import ClassCreate
        from modules.class_mgmt.services import ClassMgmtService

        db = MagicMock()
        grade = MagicMock()
        grade.school_id = 1
        db.get = AsyncMock(return_value=grade)

        # Mock 已有同名班级
        existing_cls = MagicMock()
        existing_mock = MagicMock()
        existing_mock.scalar_one_or_none = MagicMock(return_value=existing_cls)
        db.execute = AsyncMock(return_value=existing_mock)

        data = ClassCreate(name="2501", grade_id=1)

        with pytest.raises(ValueError, match="班级名已存在"):
            await ClassMgmtService.create_class(db, 1, data)


# ── 测试学生分班 ──


class TestAssignStudents:
    @pytest.mark.asyncio
    async def test_assign_students_success(self):
        """学生分班成功"""
        from modules.class_mgmt.services import ClassMgmtService

        db = MagicMock()
        cls = MagicMock()
        cls.school_id = 1
        cls.student_count = 30
        # 学生 Mock 必须带 school_id，否则会被 school 作用域校验判为"不属于本校"
        # 而进入 failed，导致 assigned==[]（原 fixture 漏设 school_id，属测试固件缺陷，非业务回归）
        student_a = MagicMock()
        student_a.school_id = 1
        student_b = MagicMock()
        student_b.school_id = 1
        db.get = AsyncMock(side_effect=[cls, student_a, student_b])

        db.add = MagicMock()

        result = await ClassMgmtService.assign_students(
            db,
            1,
            1,
            [101, 102],
            operated_by=1,
            operator_name="管理员",
        )

        assert result["assigned"] == [101, 102]
        assert result["failed"] == []
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_assign_students_partial_failure(self):
        """部分学生分配失败"""
        from modules.class_mgmt.services import ClassMgmtService

        db = MagicMock()
        cls = MagicMock()
        cls.school_id = 1
        cls.student_count = 30
        # 第一个学生存在，第二个不存在
        student1 = MagicMock()
        student1.school_id = 1
        db.get = AsyncMock(side_effect=[cls, student1, None])

        db.add = MagicMock()

        result = await ClassMgmtService.assign_students(
            db,
            1,
            1,
            [101, 999],
            operated_by=1,
            operator_name="管理员",
        )

        assert result["assigned"] == [101]
        assert len(result["failed"]) == 1
        assert result["failed"][0]["student_id"] == 999


# ── 测试调班 ──


class TestTransferStudent:
    @pytest.mark.asyncio
    async def test_transfer_success(self):
        """调班成功"""
        from modules.class_mgmt.services import ClassMgmtService

        db = MagicMock()

        student = MagicMock()
        student.school_id = 1
        student.class_id = 1  # 原班级

        from_cls = MagicMock()
        from_cls.student_count = 40

        target_cls = MagicMock()
        target_cls.school_id = 1
        target_cls.student_count = 35
        target_cls.grade_id = 1

        db.get = AsyncMock(side_effect=[student, target_cls, from_cls])
        db.add = MagicMock()

        result = await ClassMgmtService.transfer_student(
            db,
            1,
            101,
            2,
            operated_by=1,
            operator_name="管理员",
        )

        assert result["student_id"] == 101
        assert result["from_class_id"] == 1
        assert result["to_class_id"] == 2
        # 验证人数变化
        assert from_cls.student_count == 39  # 40-1
        assert target_cls.student_count == 36  # 35+1

    @pytest.mark.asyncio
    async def test_transfer_student_not_found(self):
        """学生不存在"""
        from modules.class_mgmt.services import ClassMgmtService

        db = MagicMock()
        db.get = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="学生不存在"):
            await ClassMgmtService.transfer_student(
                db,
                1,
                999,
                2,
                operated_by=1,
            )


# ── 测试班主任分配 ──


class TestAssignTeacher:
    @pytest.mark.asyncio
    async def test_assign_teacher_success(self):
        """班主任分配成功"""
        from modules.class_mgmt.services import ClassMgmtService

        db = MagicMock()
        cls = MagicMock()
        cls.school_id = 1
        cls.head_teacher_id = None

        teacher = MagicMock()
        teacher.school_id = 1

        db.get = AsyncMock(side_effect=[cls, teacher])
        db.add = MagicMock()

        result = await ClassMgmtService.assign_head_teacher(
            db,
            1,
            1,
            101,
            operated_by=1,
            operator_name="管理员",
        )

        assert result.head_teacher_id == 101


# ── 测试统计 ──


class TestClassStats:
    @pytest.mark.asyncio
    async def test_get_stats(self):
        """班级统计"""
        from modules.class_mgmt.services import ClassMgmtService

        db = MagicMock()

        # Mock 各查询
        count_mock = MagicMock()
        count_mock.scalar = MagicMock(return_value=8)

        student_count_mock = MagicMock()
        student_count_mock.scalar = MagicMock(return_value=389)

        grade_result = [(MagicMock(), MagicMock())]
        grade_result[0] = ("七年级", 8, 389)

        class_result_mock = MagicMock()
        class_list = []
        cls_max = MagicMock()
        cls_max.id = 1
        cls_max.name = "2508"
        cls_max.student_count = 55
        cls_min = MagicMock()
        cls_min.id = 2
        cls_min.name = "2502"
        cls_min.student_count = 42
        class_list.append((cls_max, "七年级"))
        class_list.append((cls_min, "七年级"))
        class_result_mock.__iter__ = MagicMock(return_value=iter(class_list))

        db.execute = AsyncMock(
            side_effect=[count_mock, student_count_mock, MagicMock(), class_result_mock]
        )

        result = await ClassMgmtService.get_stats(db, 1)

        assert result["total_classes"] == 8
        assert result["total_students"] == 389
        assert result["avg_class_size"] == 48.6  # 389/8
