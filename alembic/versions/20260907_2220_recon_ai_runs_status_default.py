"""recon: ai_runs.status server_default (E1-T4, staging-first)

R2b reconciliation — 仅补 ai_runs.status 的 DEFAULT 'PLANNING'。

取证事实（2026-09-07 BREAK-GLASS R0）：
  - 20260811_1810 定义 ai_runs.status 为 8 态 ENUM（PLANNING..FAILED），
    server_default='PLANNING'；
  - 双库实际 COLUMN_DEFAULT = NULL（DEFAULT 缺失 = 唯一 mismatch，ops=165/1）；
  - ⚠ 20260815_1130（FT-015）曾把 ENUM 扩至 9 态（+CANCELLED，生产手工 ALTER
    既成事实后写回历史）。因此**实际枚举可能是 8 态或 9 态**，
    本 migration 不做任何假设。

安全纪律（周主任 E1 硬门）：
  - 只补 DEFAULT。使用 MySQL 原生 `ALTER TABLE ... ALTER COLUMN ... SET DEFAULT`，
    **完全不触碰列类型/ENUM 成员/nullable/列名/现有行值**；
  - 执行前后 COLUMN_TYPE 原文逐字节比对，任何变化 → RAISE（E1=FAIL）；
  - 执行前后 status 数据分布比对，任何变化 → RAISE（E1=FAIL）；
  - 幂等：DEFAULT 已为 PLANNING 则 no-op；
  - 拒绝 sa.Enum/alter_column 路径（杜绝"顺手重建 enum"）。

Revision ID: 20260907_2220
Revises: 20260907_2210
Create Date: 2026-09-07

★ E1 批准链：周主任拍板 A′。Git 锻造，禁止服务器现场修改。
  ⚠ ENV GUARD 锁死 staging（wings3_staging@3308）；production 同序（R6）须
  另出经批准的修订版 migration，不得直接复用本文件。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_2220"
down_revision: str | None = "20260907_2210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPECTED_DEFAULT = "PLANNING"


def _column_meta(bind) -> tuple[str, str | None]:
    """返回 (COLUMN_TYPE 原文, COLUMN_DEFAULT)。"""
    row = bind.execute(
        sa.text(
            "SELECT COLUMN_TYPE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_runs' "
            "AND COLUMN_NAME = 'status'"
        )
    ).fetchone()
    return ((row[0] or "") if row else "", row[1] if row else None)


def _distribution(bind) -> list[tuple]:
    return bind.execute(
        sa.text("SELECT status, COUNT(*) FROM ai_runs GROUP BY status ORDER BY status")
    ).fetchall()


def upgrade() -> None:
    bind = op.get_bind()

    # ── 0) 环境身份断言（TRAP 防线：只允许 wings3_staging / 3308）──
    db_name = bind.execute(sa.text("SELECT DATABASE()")).scalar()
    db_port = bind.execute(sa.text("SELECT @@port")).scalar()
    if db_name != "wings3_staging" or int(db_port or 0) != 3308:
        raise RuntimeError(
            f"ENV GUARD ABORT: 本 migration 仅允许 staging "
            f"(db=wings3_staging, port=3308)，实际 db={db_name}, port={db_port}"
        )

    # ── 1) 前置取证：ENUM 原文 + 数据分布（如实记录，不假设 8/9 态）──
    enum_before, default_before = _column_meta(bind)
    dist_before = _distribution(bind)

    if default_before == EXPECTED_DEFAULT:
        # 幂等：DEFAULT 已正确 → no-op（不重复 DDL）
        return

    # ── 2) 仅补 DEFAULT（MySQL 原生语法，不触碰 ENUM/类型/数据）──
    op.execute(
        sa.text(
            f"ALTER TABLE ai_runs ALTER COLUMN status "
            f"SET DEFAULT '{EXPECTED_DEFAULT}'"
        )
    )

    # ── 3) 后置硬断言：枚举原文不变 + 数据分布不变 + default 正确 ──
    enum_after, default_after = _column_meta(bind)
    dist_after = _distribution(bind)

    if enum_after != enum_before:
        raise RuntimeError(
            f"R2b FAIL: ENUM 定义被改动！\n"
            f"  before: {enum_before}\n  after:  {enum_after}"
        )
    if [tuple(r) for r in dist_after] != [tuple(r) for r in dist_before]:
        raise RuntimeError(
            f"R2b FAIL: ai_runs.status 数据分布发生变化！\n"
            f"  before: {dist_before}\n  after:  {dist_after}"
        )
    if default_after != EXPECTED_DEFAULT:
        raise RuntimeError(
            f"R2b FAIL: DEFAULT 未生效（expected={EXPECTED_DEFAULT}, "
            f"actual={default_after}）"
        )


def downgrade() -> None:
    """对称回退：仅移除 DEFAULT（ENUM/数据不触碰）。"""
    bind = op.get_bind()
    db_name = bind.execute(sa.text("SELECT DATABASE()")).scalar()
    db_port = bind.execute(sa.text("SELECT @@port")).scalar()
    if db_name != "wings3_staging" or int(db_port or 0) != 3308:
        raise RuntimeError(
            f"ENV GUARD ABORT: downgrade 同样仅允许 staging "
            f"(db={db_name}, port={db_port})"
        )
    op.execute(sa.text("ALTER TABLE ai_runs ALTER COLUMN status DROP DEFAULT"))
