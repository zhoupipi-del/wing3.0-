"""
20260908_0015 — port discipline closure (G5 semantics) onto canonical recovery line

背景
----
G5「处分闭环」原本以 revision `g5_20260824` 存在于 492d70bc 应用线，
但 staging 当前 alembic_version = 20260907_2220，**从未执行过 g5**，
且 492d70bc 与 canonical 线（0394230）是 DIVERGED 兄弟分支（merge-base edd5df8）。

因此本 revision **不复用 g5 的 revision ID** —— 若把原 g5 塞进 2210 之前，
会制造「账本显示 g5 是祖先、实际从未执行」的假历史。

本 revision 是一个 **reconciliation / port migration**，挂在 20260907_2220 之后，
使得：
  * 当前 staging (2220) 升级时会真正执行它（因为是后代，不会被当作祖先跳过）
  * fresh DB 按 canonical 新链顺序执行
  * 未来 production 也能依据真实状态安全收敛

语义
----
discipline_sanctions.status ENUM: 6 态 → 8 态
    + PARENT_COMMUNICATED  (家长已沟通，家校闭环)
    + CLOSED               (结案归档，处分生命周期终结)

新增成员**追加在末尾**，保证既有数据的 ENUM 存储索引不变。

安全约束
--------
* 精确比对 ENUM 成员集合与顺序，绝不只比数量、绝不只检查"是否包含新值"
* 遇到非预期的 ENUM 形态（如 7 态 / 9 态 / 顺序不同）一律 FAIL，不自动覆盖
* downgrade 极其保守：仅当 PARENT_COMMUNICATED / CLOSED 行数为 0 才允许收回 6 态，
  否则 ABORT —— MySQL 收缩 ENUM 会损伤超出范围的数据
"""
from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20260908_0015"
down_revision = "20260907_2220"
branch_labels = None
depends_on = None

TABLE = "discipline_sanctions"
COLUMN = "status"

# 精确锁定：canonical 当前 6 态（顺序即 DB 实际顺序）
CUR_6 = (
    "DRAFT_PENDING",
    "PENDING",
    "GRADE_LEADER_APPROVED",
    "ACTIVE",
    "REJECTED",
    "REVOKED",
)
# 目标 8 态：新增成员追加末尾
TGT_8 = CUR_6 + ("PARENT_COMMUNICATED", "CLOSED")

NEW_STATES = ("PARENT_COMMUNICATED", "CLOSED")


# ------------------------------------------------------------ 纯逻辑（可单测）

def parse_enum(coltype: str) -> tuple[str, ...] | None:
    """从 information_schema 的 COLUMN_TYPE 解析 ENUM 成员，保持顺序。"""
    if not coltype:
        return None
    m = re.match(r"^\s*enum\((.*)\)\s*$", coltype, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    return tuple(re.findall(r"'((?:[^']|'')*)'", m.group(1)))


def resolve_upgrade(current: tuple[str, ...] | None) -> str:
    """
    返回 'UPGRADE' | 'NOOP' | 'FAIL'
    只认精确的 6 态与 8 态，其余一律 FAIL。
    """
    if current is None:
        return "FAIL"
    if current == CUR_6:
        return "UPGRADE"
    if current == TGT_8:
        return "NOOP"
    return "FAIL"


def resolve_downgrade(current: tuple[str, ...] | None, new_state_rows: int) -> str:
    """
    返回 'DOWNGRADE' | 'NOOP' | 'ABORT' | 'FAIL'
    存在新状态数据时绝不收缩 ENUM。
    """
    if current is None:
        return "FAIL"
    if current == CUR_6:
        return "NOOP"
    if current != TGT_8:
        return "FAIL"
    if new_state_rows > 0:
        return "ABORT"
    return "DOWNGRADE"


# ------------------------------------------------------------ 读写 DB 辅助

def _current_members() -> tuple[str, ...] | None:
    row = op.get_bind().execute(
        sa.text(
            """
            SELECT COLUMN_TYPE
              FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE()
               AND TABLE_NAME   = :t
               AND COLUMN_NAME  = :c
            """
        ),
        {"t": TABLE, "c": COLUMN},
    ).fetchone()
    if not row:
        return None
    return parse_enum(str(row[0]))


def _count_new_state_rows() -> int:
    placeholders = ", ".join(f":s{i}" for i in range(len(NEW_STATES)))
    sql = f"SELECT COUNT(*) FROM `{TABLE}` WHERE `{COLUMN}` IN ({placeholders})"
    params = {f"s{i}": s for i, s in enumerate(NEW_STATES)}
    row = op.get_bind().execute(sa.text(sql), params).fetchone()
    return int(row[0]) if row else 0


# ------------------------------------------------------------ migration 主体

def upgrade() -> None:
    cur = _current_members()
    action = resolve_upgrade(cur)
    print(f"[20260908_0015] current={cur} action={action}")

    if action == "FAIL":
        raise RuntimeError(
            f"[20260908_0015] UNEXPECTED ENUM on {TABLE}.{COLUMN}: {cur}. "
            f"expected exactly {CUR_6} or {TGT_8}. refusing to auto-fix."
        )
    if action == "NOOP":
        print("[20260908_0015] already 8-state, verify-only no-op")
        return

    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=mysql.ENUM(*CUR_6, collation="utf8mb4_unicode_ci"),
        type_=mysql.ENUM(*TGT_8, collation="utf8mb4_unicode_ci"),
        existing_nullable=False,
        existing_server_default=None,
        existing_comment="生命周期状态",
        comment=(
            "生命周期状态: "
            "DRAFT_PENDING/PENDING/GRADE_LEADER_APPROVED/ACTIVE/"
            "PARENT_COMMUNICATED/CLOSED/REJECTED/REVOKED"
        ),
    )

    after = _current_members()
    if after != TGT_8:
        raise RuntimeError(f"[20260908_0015] VERIFY FAILED after upgrade: {after}")
    print("[20260908_0015] upgraded 6-state -> 8-state OK")


def downgrade() -> None:
    cur = _current_members()
    rows = _count_new_state_rows()
    action = resolve_downgrade(cur, rows)
    print(f"[20260908_0015 down] current={cur} new_state_rows={rows} action={action}")

    if action == "FAIL":
        raise RuntimeError(
            f"[20260908_0015 down] UNEXPECTED ENUM: {cur}; refusing to downgrade."
        )
    if action == "ABORT":
        raise RuntimeError(
            f"[20260908_0015 down] ABORT: {rows} row(s) still in {NEW_STATES}. "
            f"shrinking ENUM would corrupt data. migrate/clean those rows first."
        )
    if action == "NOOP":
        print("[20260908_0015 down] already 6-state, no-op")
        return

    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=mysql.ENUM(*TGT_8, collation="utf8mb4_unicode_ci"),
        type_=mysql.ENUM(*CUR_6, collation="utf8mb4_unicode_ci"),
        existing_nullable=False,
        existing_server_default=None,
        existing_comment="生命周期状态",
        comment="生命周期状态: DRAFT_PENDING/PENDING/ACTIVE/REJECTED/REVOKED",
    )

    after = _current_members()
    if after != CUR_6:
        raise RuntimeError(f"[20260908_0015 down] VERIFY FAILED: {after}")
    print("[20260908_0015 down] reverted 8-state -> 6-state OK")
