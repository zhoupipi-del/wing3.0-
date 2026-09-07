"""
20260908_0015 port_discipline_closure 自测

覆盖 BOSS 指定的关键场景：
  migration: 6→8 PASS / 8→8 no-op / 未知态 FAIL / 存在新状态数据时 downgrade ABORT
  semantics: 模型 8 态、两个状态转换存在、role guard 保留
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "alembic" / "versions"))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "port_closure", ROOT / "alembic" / "versions" / "20260908_0015_port_discipline_closure.py"
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

CUR_6 = mod.CUR_6
TGT_8 = mod.TGT_8


# ------------------------------------------------------------------ parse_enum

def test_parse_enum_basic():
    assert mod.parse_enum("enum('A','B','C')") == ("A", "B", "C")


def test_parse_enum_real_column_type():
    ct = (
        "enum('DRAFT_PENDING','PENDING','GRADE_LEADER_APPROVED',"
        "'ACTIVE','REJECTED','REVOKED')"
    )
    assert mod.parse_enum(ct) == CUR_6


def test_parse_enum_non_enum_returns_none():
    assert mod.parse_enum("varchar(40)") is None
    assert mod.parse_enum("") is None
    assert mod.parse_enum(None) is None


# ------------------------------------------------------------- resolve_upgrade

def test_upgrade_6state_upgrades():
    assert mod.resolve_upgrade(CUR_6) == "UPGRADE"


def test_upgrade_8state_noop():
    assert mod.resolve_upgrade(TGT_8) == "NOOP"


def test_upgrade_unknown_7state_fails():
    seven = CUR_6 + ("PARENT_COMMUNICATED",)
    assert mod.resolve_upgrade(seven) == "FAIL"


def test_upgrade_unknown_9state_fails():
    nine = TGT_8 + ("MYSTERY",)
    assert mod.resolve_upgrade(nine) == "FAIL"


def test_upgrade_reordered_fails():
    reordered = tuple(reversed(CUR_6))
    assert mod.resolve_upgrade(reordered) == "FAIL"


def test_upgrade_missing_column_fails():
    assert mod.resolve_upgrade(None) == "FAIL"


def test_upgrade_does_not_accept_superset_by_containment():
    """不能只因为'包含新枚举'就判定无需升级 —— 7 态必须 FAIL。"""
    assert "PARENT_COMMUNICATED" in (CUR_6 + ("PARENT_COMMUNICATED",))
    assert mod.resolve_upgrade(CUR_6 + ("PARENT_COMMUNICATED",)) == "FAIL"


# ----------------------------------------------------------- resolve_downgrade

def test_downgrade_8state_no_new_rows_downgrades():
    assert mod.resolve_downgrade(TGT_8, 0) == "DOWNGRADE"


def test_downgrade_8state_with_new_rows_aborts():
    assert mod.resolve_downgrade(TGT_8, 5) == "ABORT"


def test_downgrade_never_shrinks_with_data():
    for n in (1, 2, 100):
        assert mod.resolve_downgrade(TGT_8, n) == "ABORT"


def test_downgrade_6state_noop():
    assert mod.resolve_downgrade(CUR_6, 0) == "NOOP"


def test_downgrade_unknown_fails():
    assert mod.resolve_downgrade(CUR_6 + ("X",), 0) == "FAIL"


# -------------------------------------------------------------- 代码语义静态校验

def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_model_has_8_state_enum():
    src = _read("modules/discipline/models.py")
    assert "PARENT_COMMUNICATED" in src
    assert "CLOSED" in src
    for m in ("DRAFT_PENDING", "PENDING", "GRADE_LEADER_APPROVED",
              "ACTIVE", "REJECTED", "REVOKED"):
        assert re.search(rf"^\s+{m}\s*=", src, re.M), f"missing enum member {m}"


def test_services_has_both_transitions():
    src = _read("modules/discipline/services.py")
    assert "def mark_parent_communicated" in src
    assert "def close_sanction" in src


def test_routers_has_both_endpoints():
    src = _read("modules/discipline/routers.py")
    assert "/parent-communicated" in src
    assert "/close" in src


def test_role_guard_preserved():
    src = _read("modules/discipline/routers.py")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if node.name in ("mark_parent_communicated", "close_sanction"):
                seg = ast.get_source_segment(src, node) or ""
                assert "require_role" in seg, f"{node.name} missing role guard"
                found = True
    assert found, "endpoints not found"


def test_endpoints_are_post_only():
    src = _read("modules/discipline/routers.py")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if node.name in ("mark_parent_communicated", "close_sanction"):
                for dec in node.decorator_list:
                    seg = ast.get_source_segment(src, dec) or ""
                    assert "post" in seg.lower(), f"{node.name} must be POST"


# -------------------------------------------------------------- migration 元数据

def test_revision_metadata():
    assert mod.revision == "20260908_0015"
    assert mod.down_revision == "20260907_2220"


def test_new_states_appended_at_end():
    """新增成员必须追加在末尾，否则既有数据存储索引会错位。"""
    assert TGT_8[:6] == CUR_6
    assert TGT_8[6:] == ("PARENT_COMMUNICATED", "CLOSED")


def test_orm_enum_order_equals_migration_target():
    """
    ORM 枚举成员顺序必须与 migration TGT_8 完全一致。

    这条测试存在的理由：MySQL ENUM 按位置存储索引。若 ORM 把 PARENT_COMMUNICATED
    插在 ACTIVE 与 REJECTED 之间，而 migration 追加在末尾，二者定义的同一下标
    会指向不同状态 —— 既有 REJECTED/REVOKED 数据可能静默错位。
    """
    src = _read("modules/discipline/models.py")
    tree = ast.parse(src)
    members = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "DisciplineStatus":
            members = []
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name):
                            members.append(tgt.id)
            break
    assert members is not None, "DisciplineStatus not found"
    assert tuple(members) == TGT_8, (
        f"ORM enum order {tuple(members)} != migration target {TGT_8}"
    )


def test_existing_six_states_keep_their_positions():
    """既有 6 态在新枚举中的下标必须保持不变（1..6），否则老数据错位。"""
    for i, state in enumerate(CUR_6):
        assert TGT_8[i] == state, f"{state} moved from index {i} to {TGT_8.index(state)}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--no-header"]))
