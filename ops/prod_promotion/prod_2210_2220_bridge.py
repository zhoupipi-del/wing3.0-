"""prod_2210_2220_bridge — PROD-SEMANTIC-BRIDGE 6 阶段显式状态机。

PROD-SEMANTIC-BRIDGE-CANDIDATE-001（LOCAL/GIT ONLY，禁止生产连接写/stamp/deploy）。

职责边界（周主任硬门）：
    cf04ops → physical2210 → verify → stamp2210 → physical2220 → verify → stamp2220
之后 0015 / 2230 回到 canonical Alembic 正常逐 revision 执行，本工具不碰、不混合。

设计纪律：
  - 禁止一个命令自动跑完整链。每阶段是独立子命令，须由上一步 evidence 才能进入下一步。
  - stamp 不执行 DDL；仅在物理 schema 等价验证后恢复 Alembic bookkeeping。
  - 2210 语义严格复刻原 20260907_2210（warning_feedback.warning_id → risk_warnings.id → school_id），
    FK=fk_warning_feedback_school_id → schools.id（无 ondelete），index=ix_warning_feedback_school_id。
  - 2220 仅 SET DEFAULT 'PLANNING'，原生 ALTER，前后逐字节比对 COLUMN_TYPE 与 status 分布，
    任何变化 → RAISE（不触碰 ENUM/nullable/行值）。
  - 即便生产当前 0 行，回填代码与 unresolved gate 必须保留（可复用、可审计 bridge，非一次性 SQL）。

生产硬 guard 在 preflight 模块；本模块的每个 mutating 阶段都先调 require_prod_guard + require_head。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prod_promotion_preflight import (
    PromotionContext,
    ProdGuardError,
    require_prod_guard,
    require_head,
    require_approve,
    load_production_db,
    HEAD_CF04OPS,
    HEAD_2210,
    REV_2210,
    REV_2220,
)

# 与原 2210 migration 完全一致的命名，避免 ibfk_N 自动命名漂移
FK_NAME = "fk_warning_feedback_school_id"
IX_NAME = "ix_warning_feedback_school_id"
EXPECTED_DEFAULT = "PLANNING"


# ═══════════════════════════════════════════════════════════════════════════
# 纯决策函数（可脱离 DB 单测）
# ═══════════════════════════════════════════════════════════════════════════
def decide_2210(db) -> dict:
    """返回 {decision: APPLY | VERIFIED | FAIL, reason}。

    - 列缺失            → APPLY（ADD + 回填 + FK + index）
    - 列存在但 type/FK/index 不符或存在 unresolved 行 → FAIL（不静默修复）
    - 列存在且 BIGINT / FK / index / 无 unresolved → VERIFIED（no-op）
    """
    if not db.column_exists("warning_feedback", "school_id"):
        return {"decision": "APPLY", "reason": "column absent → ADD+backfill+FK+index"}
    ctype = db.column_type("warning_feedback", "school_id").lower()
    if "bigint" not in ctype:
        return {"decision": "FAIL", "reason": f"school_id type 非 BIGINT: {ctype}"}
    if not db.fk_exists("warning_feedback", "school_id", FK_NAME, "schools"):
        return {"decision": "FAIL", "reason": "FK 缺失 / 不符"}
    if not db.index_exists("warning_feedback", "school_id", IX_NAME):
        return {"decision": "FAIL", "reason": "index 缺失 / 不符"}
    if db.null_count("warning_feedback", "school_id") != 0:
        return {"decision": "FAIL", "reason": "已存在 unresolved 行（禁止默认填值）"}
    return {"decision": "VERIFIED", "reason": "列已存在且 type/FK/index 完全等价、无 unresolved"}


def decide_2220(db) -> dict:
    """返回 {decision: APPLY | VERIFIED | FAIL, reason}。"""
    ctype = db.column_type("ai_runs", "status")
    if "enum" not in ctype.lower():
        return {"decision": "FAIL", "reason": f"ai_runs.status 非 ENUM: {ctype}"}
    default = db.column_default("ai_runs", "status")
    if default == EXPECTED_DEFAULT:
        return {"decision": "VERIFIED", "reason": f"DEFAULT 已是 {EXPECTED_DEFAULT}"}
    return {"decision": "APPLY", "reason": f"DEFAULT={default} → SET '{EXPECTED_DEFAULT}'"}


# ═══════════════════════════════════════════════════════════════════════════
# 阶段实现（每阶段独立；证据落盘供下一阶段进程接力）
# ═══════════════════════════════════════════════════════════════════════════
def stage_preflight(ctx: PromotionContext) -> dict:
    guard = require_prod_guard(ctx)
    head = ctx.db.alembic_version()
    payload = {**guard, "alembic_head": head, "stage": "preflight"}
    ctx.evidence("preflight").write(payload)
    return payload


def stage_apply_2210(ctx: PromotionContext) -> dict:
    require_prod_guard(ctx)
    require_head(ctx, HEAD_CF04OPS)
    d = decide_2210(ctx.db)
    if d["decision"] == "FAIL":
        raise ProdGuardError(f"apply-2210 拒绝: {d['reason']}")

    if d["decision"] == "APPLY":
        # 1) 声明式 ADD COLUMN（nullable，无 default；已存在则 skip）
        if not ctx.db.column_exists("warning_feedback", "school_id"):
            ctx.db.exec_ddl(
                "ALTER TABLE warning_feedback ADD COLUMN school_id BIGINT NULL"
            )
        # 2) 按确定组织关系回填（复刻原 2210 语义）
        ctx.db.exec_sql(
            "UPDATE warning_feedback wf "
            "JOIN risk_warnings rw ON wf.warning_id = rw.id "
            "SET wf.school_id = rw.school_id "
            "WHERE wf.school_id IS NULL"
        )
        # 3) 硬断言：unresolved 必须为 0
        unresolved = ctx.db.null_count("warning_feedback", "school_id")
        if unresolved != 0:
            raise ProdGuardError(
                f"R1 ABORT: warning_feedback 存在 {unresolved} 行无法唯一推导 "
                f"school_id。禁止默认填值，需人工对账后重跑。"
            )
        # 4) FK + index（断言通过后才建立）
        if not ctx.db.fk_exists("warning_feedback", "school_id", FK_NAME, "schools"):
            ctx.db.exec_ddl(
                f"ALTER TABLE warning_feedback ADD CONSTRAINT {FK_NAME} "
                f"FOREIGN KEY (school_id) REFERENCES schools(id)"
            )
        if not ctx.db.index_exists("warning_feedback", "school_id", IX_NAME):
            ctx.db.exec_ddl(
                f"ALTER TABLE warning_feedback ADD INDEX {IX_NAME} (school_id)"
            )

    payload = {
        "decision": d["decision"],
        "unresolved": ctx.db.null_count("warning_feedback", "school_id"),
        "row_count": ctx.db.row_count("warning_feedback"),
        "stage": "apply_2210",
    }
    ctx.evidence("apply_2210").write(payload)
    return payload


def stage_verify_2210(ctx: PromotionContext) -> dict:
    checks = {
        "column_exists": ctx.db.column_exists("warning_feedback", "school_id"),
        "type_bigint": "bigint"
        in ctx.db.column_type("warning_feedback", "school_id").lower(),
        "nullable": ctx.db.column_nullable("warning_feedback", "school_id"),
        "fk": ctx.db.fk_exists("warning_feedback", "school_id", FK_NAME, "schools"),
        "index": ctx.db.index_exists("warning_feedback", "school_id", IX_NAME),
        "null_count": ctx.db.null_count("warning_feedback", "school_id"),
        "row_count": ctx.db.row_count("warning_feedback"),
    }
    passed = (
        checks["column_exists"]
        and checks["type_bigint"]
        and checks["nullable"]
        and checks["fk"]
        and checks["index"]
        and checks["null_count"] == 0
    )
    payload = {"checks": checks, "verified": passed, "stage": "verify_2210"}
    ctx.evidence("verify_2210").write(payload)
    if not passed:
        raise ProdGuardError(f"verify-2210 FAIL: {checks}")
    return payload


def stage_stamp_2210(ctx: PromotionContext) -> dict:
    require_prod_guard(ctx)
    require_head(ctx, HEAD_CF04OPS)
    ev = ctx.evidence("verify_2210").read()
    if not ev or not ev.get("verified"):
        raise ProdGuardError(
            "stamp-2210 DENY: 2210 物理语义未验证（verify-2210 evidence 缺失/未过）"
        )
    # stamp 仅恢复 alembic bookkeeping，不执行 DDL
    ctx.db.set_alembic_version(REV_2210)
    after = ctx.db.alembic_version()
    if after != REV_2210:
        raise ProdGuardError(f"stamp-2210 STOP: alembic_version={after} != {REV_2210}")
    payload = {"stamped": REV_2210, "alembic_version": after, "stage": "stamp_2210"}
    ctx.evidence("stamp_2210").write(payload)
    return payload


def stage_apply_2220(ctx: PromotionContext) -> dict:
    require_prod_guard(ctx)
    require_head(ctx, HEAD_2210)
    d = decide_2220(ctx.db)
    if d["decision"] == "FAIL":
        raise ProdGuardError(f"apply-2220 拒绝: {d['reason']}")

    enum_before = ctx.db.column_type("ai_runs", "status")
    dist_before = ctx.db.status_distribution("ai_runs", "status")

    if d["decision"] == "VERIFIED":
        payload = {
            "decision": "VERIFIED",
            "enum_before": enum_before,
            "enum_after": enum_before,
            "dist_before": dist_before,
            "dist_after": dist_before,
            "stage": "apply_2220",
        }
        ctx.evidence("apply_2220").write(payload)
        return payload

    # APPLY：仅 SET DEFAULT，不触碰 ENUM / 类型 / 数据
    ctx.db.exec_ddl(
        f"ALTER TABLE ai_runs ALTER COLUMN status SET DEFAULT '{EXPECTED_DEFAULT}'"
    )
    payload = {
        "decision": "APPLY",
        "enum_before": enum_before,
        "dist_before": dist_before,
        "stage": "apply_2220",
    }
    ctx.evidence("apply_2220").write(payload)
    return payload


def stage_verify_2220(ctx: PromotionContext) -> dict:
    ev = ctx.evidence("apply_2220").read()
    if not ev:
        raise ProdGuardError("verify-2220 FAIL: apply-2220 evidence 缺失")
    enum_before = ev.get("enum_before")
    dist_before = ev.get("dist_before") or []
    enum_after = ctx.db.column_type("ai_runs", "status")
    dist_after = ctx.db.status_distribution("ai_runs", "status")
    default_after = ctx.db.column_default("ai_runs", "status")

    checks = {
        "enum_unchanged": enum_after == enum_before,
        "dist_unchanged": [tuple(r) for r in dist_after]
        == [tuple(r) for r in dist_before],
        "default_planning": default_after == EXPECTED_DEFAULT,
    }
    passed = all(checks.values())
    payload = {
        "checks": checks,
        "default": default_after,
        "verified": passed,
        "stage": "verify_2220",
    }
    ctx.evidence("verify_2220").write(payload)
    if not passed:
        raise ProdGuardError(f"verify-2220 FAIL: {checks}")
    return payload


def stage_stamp_2220(ctx: PromotionContext) -> dict:
    require_prod_guard(ctx)
    require_head(ctx, HEAD_2210)
    ev = ctx.evidence("verify_2220").read()
    if not ev or not ev.get("verified"):
        raise ProdGuardError(
            "stamp-2220 DENY: 2220 物理语义未验证（verify-2220 evidence 缺失/未过）"
        )
    ctx.db.set_alembic_version(REV_2220)
    after = ctx.db.alembic_version()
    if after != REV_2220:
        raise ProdGuardError(f"stamp-2220 STOP: alembic_version={after} != {REV_2220}")
    payload = {"stamped": REV_2220, "alembic_version": after, "stage": "stamp_2220"}
    ctx.evidence("stamp_2220").write(payload)
    return payload


_STAGES = {
    "preflight": stage_preflight,
    "apply-2210": stage_apply_2210,
    "verify-2210": stage_verify_2210,
    "stamp-2210": stage_stamp_2210,
    "apply-2220": stage_apply_2220,
    "verify-2220": stage_verify_2220,
    "stamp-2220": stage_stamp_2220,
}


def main() -> None:
    p = argparse.ArgumentParser(
        description="PROD-SEMANTIC-BRIDGE 6 阶段状态机（须 WINGS_PROD_PROMOTION=APPROVED）"
    )
    p.add_argument(
        "stage",
        choices=list(_STAGES.keys()),
        help="显式阶段；禁止一个命令跑完整链",
    )
    p.add_argument("--evidence-dir", default=str(Path("/var/log/wings3/prod_promotion")))
    p.add_argument("--dry-run", action="store_true", help="不连生产（仅用于结构校验）")
    args = p.parse_args()

    if args.dry_run:
        print(json.dumps({"result": "DRY_RUN", "stage": args.stage}, ensure_ascii=False))
        return

    ctx = PromotionContext(
        db=load_production_db(),
        approved=require_approve(),
        evidence_dir=Path(args.evidence_dir),
    )
    try:
        result = _STAGES[args.stage](ctx)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ProdGuardError as e:
        print(
            json.dumps({"result": "ABORT", "error": str(e)}, ensure_ascii=False),
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
