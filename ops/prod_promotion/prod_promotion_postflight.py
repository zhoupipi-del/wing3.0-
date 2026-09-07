"""prod_promotion_postflight — bridge 段完成后 coherence 校验。

PROD-SEMANTIC-BRIDGE-CANDIDATE-001（LOCAL/GIT ONLY）。

职责边界（周主任硬门）：
  本文件只校验 bridge 已把生产从 cf04ops 接回 20260907_2220，且 2210/2220 物理语义就位。
  0015 / 2230 由 canonical Alembic 正常逐 revision 执行（upgrade 20260908_0015 → upgrade 20260908_2230），
  不混入本工具。

最终生产 promotion 仍需：
  1) 同维护窗口内，bridge 完成（到 stamp-2220）
  2) alembic upgrade 20260908_0015 + 硬验
  3) alembic upgrade 20260908_2230 + 硬验
  4) 激活 canonical APP（deploy.py activate 3b226bc）
  5) 启动全部服务 + health/version/smoke + coherence gate
详见 RUNBOOK.md。
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
    load_production_db,
    HEAD_2220,
)
from prod_2210_2220_bridge import FK_NAME, IX_NAME


def stage_postflight(ctx: PromotionContext) -> dict:
    require_prod_guard(ctx)
    head = ctx.db.alembic_version()
    checks = {
        "alembic_head_is_2220": head == HEAD_2220,
        "2210_physical": (
            ctx.db.column_exists("warning_feedback", "school_id")
            and "bigint" in ctx.db.column_type("warning_feedback", "school_id").lower()
            and ctx.db.fk_exists("warning_feedback", "school_id", FK_NAME, "schools")
            and ctx.db.index_exists("warning_feedback", "school_id", IX_NAME)
            and ctx.db.null_count("warning_feedback", "school_id") == 0
        ),
        "2220_physical": ctx.db.column_default("ai_runs", "status") == "PLANNING",
    }
    passed = all(checks.values())
    payload = {
        "alembic_head": head,
        "checks": checks,
        "coherent": passed,
        "next_steps": [
            "alembic upgrade 20260908_0015 + 硬验",
            "alembic upgrade 20260908_2230 + 硬验",
            "deploy.py activate 3b226bc（激活 canonical APP）",
        ],
        "stage": "postflight",
    }
    ctx.evidence("postflight").write(payload)
    if not passed:
        raise ProdGuardError(f"postflight FAIL: {checks}")
    return payload


def main() -> None:
    p = argparse.ArgumentParser(description="PROD-SEMANTIC-BRIDGE postflight coherence 校验")
    p.add_argument("--evidence-dir", default=str(Path("/var/log/wings3/prod_promotion")))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.dry_run:
        print(json.dumps({"result": "DRY_RUN", "stage": "postflight"}, ensure_ascii=False))
        return

    ctx = PromotionContext(db=load_production_db(), evidence_dir=Path(args.evidence_dir))
    try:
        result = stage_postflight(ctx)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ProdGuardError as e:
        print(
            json.dumps({"result": "ABORT", "error": str(e)}, ensure_ascii=False),
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
