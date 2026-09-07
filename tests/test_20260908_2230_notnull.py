"""
20260908_2230 warning_feedback.school_id NOT NULL 自测

重点覆盖：
  * 五项硬断言（列存在 / BIGINT / FK / index / NULL=0）
  * NULL rows > 0 必须 FAIL —— 防止以后有人把它"优化"成危险自动回填
  * ORM 契约（SchoolMixin.school_id nullable=False）与 migration 目标一致
  * downgrade 只放宽约束、不改数据
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "notnull_2230",
    ROOT / "alembic" / "versions" / "20260908_2230_warning_feedback_school_id_not_null.py",
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


OK = dict(column_exists=True, column_type="bigint", fk_ok=True, index_ok=True,
          null_rows=0, already_not_null=False)


# ------------------------------------------------------------ upgrade 判定

def test_all_assertions_pass_upgrades():
    assert mod.resolve_upgrade(**OK) == "UPGRADE"


def test_null_rows_present_must_fail():
    """最关键：存在 NULL 数据必须 FAIL，绝不自动回填 / 不设默认值。"""
    for n in (1, 3, 999):
        assert mod.resolve_upgrade(**{**OK, "null_rows": n}) == "FAIL"


def test_null_rows_fail_even_if_everything_else_ok():
    """即便其它四项全绿，只要有 NULL 行就拒绝收紧。"""
    assert mod.resolve_upgrade(**{**OK, "null_rows": 1, "fk_ok": True,
                                  "index_ok": True}) == "FAIL"


def test_missing_column_fails():
    assert mod.resolve_upgrade(**{**OK, "column_exists": False}) == "FAIL"


def test_wrong_type_fails():
    for t in ("int", "varchar(20)", "bigint unsigned", "", None):
        assert mod.resolve_upgrade(**{**OK, "column_type": t}) == "FAIL"


def test_bigint_case_insensitive_ok():
    assert mod.resolve_upgrade(**{**OK, "column_type": "BIGINT"}) == "UPGRADE"


def test_missing_fk_fails():
    assert mod.resolve_upgrade(**{**OK, "fk_ok": False}) == "FAIL"


def test_missing_index_fails():
    assert mod.resolve_upgrade(**{**OK, "index_ok": False}) == "FAIL"


def test_already_not_null_is_noop():
    assert mod.resolve_upgrade(**{**OK, "already_not_null": True}) == "NOOP"


# ------------------------------------------------------------ downgrade 判定

def test_downgrade_from_not_null():
    assert mod.resolve_downgrade(column_exists=True, already_not_null=True) == "DOWNGRADE"


def test_downgrade_when_already_null_noop():
    assert mod.resolve_downgrade(column_exists=True, already_not_null=False) == "NOOP"


def test_downgrade_missing_column_fails():
    assert mod.resolve_downgrade(column_exists=False, already_not_null=True) == "FAIL"


# ------------------------------------------------------------ ORM 契约

def test_schoolmixin_school_id_is_not_null():
    src = _read("core/models.py")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SchoolMixin":
            seg = ast.get_source_segment(src, node) or ""
            assert "nullable=False" in seg, "SchoolMixin.school_id must be nullable=False"
            assert "ForeignKey" in seg
            assert "index=True" in seg
            found = True
    assert found, "SchoolMixin not found"


def test_warning_feedback_inherits_school_mixin():
    src = _read("modules/risk_models/models.py")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "WarningFeedback":
            bases = [getattr(b, "id", None) for b in node.bases]
            assert "SchoolMixin" in bases, f"WarningFeedback bases={bases}"
            return
    pytest.fail("WarningFeedback not found")


# ------------------------------------------------------------ migration 元数据

def test_revision_metadata():
    assert mod.revision == "20260908_2230"
    assert mod.down_revision == "20260908_0015"


def test_only_targets_warning_feedback_school_id():
    assert mod.TABLE == "warning_feedback"
    assert mod.COLUMN == "school_id"
    assert mod.EXPECTED_FK == "fk_warning_feedback_school_id"
    assert mod.EXPECTED_INDEX == "ix_warning_feedback_school_id"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--no-header"]))
