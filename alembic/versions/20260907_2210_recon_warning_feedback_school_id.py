"""recon: warning_feedback.school_id (E1-T3, staging-first)

R1 reconciliation — 修复 warning_feedback 表缺失 school_id 列的物理漂移。

取证事实（2026-09-07 BREAK-GLASS R0/R1，wings3_staging@3308 与 wings3@3307 双库同构）：
  - warning_feedback 表存在但**无 school_id 列**（2d8813121d03 baseline 建表时遗漏；
    ORM WarningFeedback(Base, SchoolMixin) 经 core/models.py:76 SchoolMixin 声明该列）
  - warning_feedback 当前行数 = 0（回填平凡成立；SQL 保留通用性覆盖未来数据）
  - 组织关系唯一追溯：warning_feedback.warning_id → risk_warnings.school_id

安全纪律（周主任 E1 硬门）：
  - ADD COLUMN 必须 nullable，禁止一步 NOT NULL，禁止 server_default（尤其禁止默认 1）
  - 回填仅允许按确定组织关系推导；任何无法唯一推导的行 → unresolved
  - unresolved != 0 时 RAISE，migration 失败回滚，绝不静默
  - FK/index 在回填+断言通过之后才建立
  - NOT NULL 收紧不在本 migration 内（留待数据稳定后独立步骤）

幂等性：列已存在则跳过 ADD（声明式，不用 stamp 掩盖）。

Revision ID: 20260907_2210
Revises: cf04ops_20260816
Create Date: 2026-09-07

★ E1 批准链：周主任拍板 A′（本地备料进 Git → wrapper 部署 staging → 一次性
  BREAK-GLASS staging-only 执行）。本文件由 Git 锻造，禁止服务器现场修改。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_2210"
down_revision: str | None = "cf04ops_20260816"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 显式命名（避免 MySQL ibfk_N 自动命名漂移，便于后续 R0 式对账）
FK_NAME = "fk_warning_feedback_school_id"
IX_NAME = "ix_warning_feedback_school_id"


def _columns(bind, table: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # ── 0) 环境身份断言（防 TRAP：只允许 wings3_staging / 3308 执行本 migration）──
    db_name = bind.execute(sa.text("SELECT DATABASE()")).scalar()
    db_port = bind.execute(sa.text("SELECT @@port")).scalar()
    if db_name != "wings3_staging" or int(db_port or 0) != 3308:
        raise RuntimeError(
            f"ENV GUARD ABORT: 本次 reconciliation 仅允许 staging "
            f"(db=wings3_staging, port=3308)，实际 db={db_name}, port={db_port}"
        )

    # ── 1) 声明式 ADD COLUMN（nullable，无 default；已存在则跳过）──
    if "school_id" not in _columns(bind, "warning_feedback"):
        op.add_column(
            "warning_feedback",
            sa.Column("school_id", sa.BigInteger(), nullable=True),
        )

    # ── 2) 按确定组织关系回填（warning_id → risk_warnings.school_id）──
    rows_before = bind.execute(
        sa.text("SELECT COUNT(*) FROM warning_feedback")
    ).scalar()
    op.execute(
        sa.text(
            """
            UPDATE warning_feedback wf
            JOIN risk_warnings rw ON wf.warning_id = rw.id
            SET wf.school_id = rw.school_id
            WHERE wf.school_id IS NULL
            """
        )
    )

    # ── 3) 硬断言：unresolved 必须为 0（无法唯一推导 school_id 的行）──
    unresolved = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM warning_feedback
            WHERE school_id IS NULL
            """
        )
    ).scalar()
    backfilled = bind.execute(
        sa.text("SELECT COUNT(*) FROM warning_feedback WHERE school_id IS NOT NULL")
    ).scalar()
    if int(unresolved or 0) != 0:
        raise RuntimeError(
            f"R1 ABORT: warning_feedback 存在 {unresolved} 行无法唯一推导 "
            f"school_id（rows_before={rows_before}, backfilled={backfilled}）。"
            f"禁止默认填值，需人工对账后重跑。"
        )

    # ── 4) FK + index（断言通过后才建立；对齐 SchoolMixin：无 ondelete）──
    fk_present = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'warning_feedback'
              AND CONSTRAINT_NAME = :fkname
              AND REFERENCED_TABLE_NAME = 'schools'
            """
        ),
        {"fkname": FK_NAME},
    ).scalar()
    if int(fk_present or 0) == 0:
        op.create_foreign_key(
            FK_NAME,
            "warning_feedback",
            "schools",
            ["school_id"],
            ["id"],
        )
    ix_present = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'warning_feedback'
              AND INDEX_NAME = :ixname
            """
        ),
        {"ixname": IX_NAME},
    ).scalar()
    if int(ix_present or 0) == 0:
        op.create_index(IX_NAME, "warning_feedback", ["school_id"])


def downgrade() -> None:
    """对称回退：仅删除本 migration 引入的 index/FK/列（若存在）。"""
    bind = op.get_bind()
    if IX_NAME in {
        r[0]
        for r in bind.execute(
            sa.text(
                "SELECT INDEX_NAME FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'warning_feedback'"
            )
        ).fetchall()
    }:
        op.drop_index(IX_NAME, table_name="warning_feedback")
    if FK_NAME in {
        r[0]
        for r in bind.execute(
            sa.text(
                "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'warning_feedback' "
                "AND REFERENCED_TABLE_NAME = 'schools'"
            )
        ).fetchall()
    }:
        op.drop_constraint(FK_NAME, "warning_feedback", type_="foreignkey")
    if "school_id" in _columns(bind, "warning_feedback"):
        op.drop_column("warning_feedback", "school_id")
