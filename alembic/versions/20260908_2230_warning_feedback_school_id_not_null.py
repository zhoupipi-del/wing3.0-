"""
20260908_2230 — warning_feedback.school_id 收紧为 NOT NULL

背景
----
ORM 契约：`SchoolMixin.school_id = Column(BigInteger, FK("schools.id"),
nullable=False, index=True)`，因此 `WarningFeedback(Base, SchoolMixin)` 要求
NOT NULL。

但 20260907_2210（R1）在 staging 落地时建成 `nullable=True`，
形成 schema contract drift。本 revision 在通过五项硬断言后把它收紧。

**本 revision 不做任何数据推断**：
  * 不回填、不猜测 school_id 默认值
  * 不使用 school_id=1 之类的兜底
  * 不触碰其它 11 处 nullable drift（那是独立的 reconciliation）
  * 只改 warning_feedback.school_id 这一个字段

若有任何一行 school_id 为 NULL → 直接 FAIL，绝不"自作主张处理"。
这是为 production 准备的：prod 若存在历史 NULL 数据，必须先人工决策回填规则，
而不是让 migration 悄悄背锅。

Revision ID : 20260908_2230
Revises     : 20260908_0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20260908_2230"
down_revision = "20260908_0015"
branch_labels = None
depends_on = None

TABLE = "warning_feedback"
COLUMN = "school_id"
EXPECTED_FK = "fk_warning_feedback_school_id"
EXPECTED_INDEX = "ix_warning_feedback_school_id"
EXPECTED_REF_TABLE = "schools"
EXPECTED_REF_COLUMN = "id"


# ------------------------------------------------------------ 纯逻辑（可单测）

def resolve_upgrade(
    *,
    column_exists: bool,
    column_type: str | None,
    fk_ok: bool,
    index_ok: bool,
    null_rows: int,
    already_not_null: bool,
) -> str:
    """
    返回 'UPGRADE' | 'NOOP' | 'FAIL'

    五项硬断言：
      1. column exists
      2. type == BIGINT
      3. FK -> schools.id exists
      4. index exists
      5. NULL count == 0        ← 任一不满足即 FAIL，绝不自动回填
    """
    if not column_exists:
        return "FAIL"
    if (column_type or "").strip().lower() != "bigint":
        return "FAIL"
    if not fk_ok:
        return "FAIL"
    if not index_ok:
        return "FAIL"
    if null_rows > 0:
        return "FAIL"
    if already_not_null:
        return "NOOP"
    return "UPGRADE"


def resolve_downgrade(*, column_exists: bool, already_not_null: bool) -> str:
    """
    返回 'DOWNGRADE' | 'NOOP' | 'FAIL'
    放宽约束不会损坏数据，因此只要列存在且当前是 NOT NULL 就允许。
    """
    if not column_exists:
        return "FAIL"
    if not already_not_null:
        return "NOOP"
    return "DOWNGRADE"


# ------------------------------------------------------------ DB 探测

def _col_info() -> tuple[bool, str | None, bool]:
    row = op.get_bind().execute(
        sa.text(
            """
            SELECT COLUMN_TYPE, IS_NULLABLE
              FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME   = :t
               AND COLUMN_NAME  = :c
            """
        ),
        {"t": TABLE, "c": COLUMN},
    ).fetchone()
    if not row:
        return False, None, False
    ctype = str(row[0]).strip().lower()
    not_null = str(row[1]).strip().upper() == "NO"
    return True, ctype, not_null


def _fk_ok() -> bool:
    rows = op.get_bind().execute(
        sa.text(
            """
            SELECT CONSTRAINT_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
              FROM information_schema.KEY_COLUMN_USAGE
             WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME   = :t
               AND COLUMN_NAME  = :c
               AND REFERENCED_TABLE_NAME IS NOT NULL
            """
        ),
        {"t": TABLE, "c": COLUMN},
    ).fetchall()
    for name, rt, rc in rows:
        if name == EXPECTED_FK and rt == EXPECTED_REF_TABLE and rc == EXPECTED_REF_COLUMN:
            return True
    return False


def _index_ok() -> bool:
    rows = op.get_bind().execute(
        sa.text(
            """
            SELECT INDEX_NAME
              FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME   = :t
               AND COLUMN_NAME  = :c
            """
        ),
        {"t": TABLE, "c": COLUMN},
    ).fetchall()
    return any(r[0] == EXPECTED_INDEX for r in rows)


def _null_rows() -> int:
    row = op.get_bind().execute(
        sa.text(f"SELECT COUNT(*) FROM `{TABLE}` WHERE `{COLUMN}` IS NULL")
    ).fetchone()
    return int(row[0]) if row else 0


def _row_count() -> int:
    row = op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM `{TABLE}`")).fetchone()
    return int(row[0]) if row else 0


# ------------------------------------------------------------ migration 主体

def upgrade() -> None:
    exists, ctype, not_null = _col_info()
    fk_ok = _fk_ok()
    idx_ok = _index_ok()
    nulls = _null_rows()
    rows_before = _row_count()

    action = resolve_upgrade(
        column_exists=exists,
        column_type=ctype,
        fk_ok=fk_ok,
        index_ok=idx_ok,
        null_rows=nulls,
        already_not_null=not_null,
    )
    print(
        f"[20260908_2230] exists={exists} type={ctype} fk={fk_ok} "
        f"index={idx_ok} nulls={nulls} not_null={not_null} -> {action}"
    )

    if action == "FAIL":
        # 尤其：nulls > 0 时必须在这里停住，让人工决定回填规则
        raise RuntimeError(
            f"[20260908_2230] REFUSING to tighten {TABLE}.{COLUMN}. "
            f"exists={exists} type={ctype} fk={fk_ok} index={idx_ok} null_rows={nulls}. "
            f"no backfill / no default value is performed by design."
        )
    if action == "NOOP":
        print("[20260908_2230] already NOT NULL, no-op")
        return

    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=mysql.BIGINT(),
        nullable=False,
        existing_nullable=True,
    )

    # 后验
    exists2, ctype2, not_null2 = _col_info()
    if not (exists2 and ctype2 == "bigint" and not_null2):
        raise RuntimeError(
            f"[20260908_2230] VERIFY FAILED: type={ctype2} not_null={not_null2}"
        )
    if not _fk_ok() or not _index_ok():
        raise RuntimeError("[20260908_2230] VERIFY FAILED: FK/index changed")
    if _row_count() != rows_before:
        raise RuntimeError("[20260908_2230] VERIFY FAILED: row_count changed")
    print(f"[20260908_2230] tightened to BIGINT NOT NULL (rows={rows_before})")


def downgrade() -> None:
    exists, ctype, not_null = _col_info()
    action = resolve_downgrade(column_exists=exists, already_not_null=not_null)
    print(f"[20260908_2230 down] exists={exists} not_null={not_null} -> {action}")

    if action == "FAIL":
        raise RuntimeError(f"[20260908_2230 down] column {TABLE}.{COLUMN} missing")
    if action == "NOOP":
        print("[20260908_2230 down] already nullable, no-op")
        return

    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=mysql.BIGINT(),
        nullable=True,
        existing_nullable=False,
    )
    print("[20260908_2230 down] relaxed to NULL (constraint widened, no data loss)")
