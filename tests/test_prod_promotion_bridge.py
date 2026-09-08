"""PROD-SEMANTIC-BRIDGE 自测（PROD-SEMANTIC-BRIDGE-CANDIDATE-001，LOCAL/GIT ONLY）。

覆盖 BOSS 指定的关键场景（不只 happy path）：
  2210: column absent+rows0 → APPLY / 可唯一回填 → APPLY / unresolved>0 → FAIL /
        column 已存在等价 → VERIFIED / type·FK·index 不同 → FAIL / current != cf04ops → FAIL
  2220: 9态+default None → APPLY / 已 PLANNING → VERIFIED / ENUM变化 → FAIL /
        distribution变化 → FAIL / current != 2210 → FAIL
  stamp: 物理语义未验证 → DENY / head 异常 → DENY
  PROD GUARD: staging db / 3308 / 未批准 / 错误 user → FAIL；production 上下文 → PASS
  secret_redactor: 连接串密码 / 敏感键 脱敏

FakeDB 实现 prod_promotion_preflight.ProdDB 接口（内存态），不连任何真实数据库。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops" / "prod_promotion"))

from prod_promotion_preflight import (  # noqa: E402
    PromotionContext,
    ProdGuardError,
    require_prod_guard,
    HEAD_CF04OPS,
    HEAD_2210,
    HEAD_2220,
    REV_2210,
    REV_2220,
)
from prod_2210_2220_bridge import (  # noqa: E402
    FK_NAME,
    IX_NAME,
    EXPECTED_DEFAULT,
    decide_2210,
    decide_2220,
    stage_preflight,
    stage_apply_2210,
    stage_verify_2210,
    stage_stamp_2210,
    stage_apply_2220,
    stage_verify_2220,
    stage_stamp_2220,
)
from prod_promotion_postflight import stage_postflight  # noqa: E402
from secret_redactor import redact_url, redact, redact_text  # noqa: E402


class FakeDB:
    """内存态 ProdDB 实现，供自测驱动 6 阶段状态机。"""

    def __init__(
        self,
        db_name="wings3",
        port=3307,
        user="grade7",
        alembic=HEAD_CF04OPS,
        server_internal_port=3306,
    ):
        self._db = db_name
        self._port = port  # DSN / 宿主映射端口（guard 判定用）
        self.server_internal_port = server_internal_port  # 模拟 SELECT @@port（容器内部端口）
        self._user = user
        self._alembic = alembic
        self.tables: dict = {}
        self.ops: list = []

    def add_table(self, name, columns, rows=None, fk=None, ix=None):
        self.tables[name] = {
            "cols": {
                c["name"]: {
                    "type": c.get("type", ""),
                    "nullable": c.get("nullable", True),
                    "default": c.get("default"),
                }
                for c in columns
            },
            "rows": rows or [],
            "fk": fk or {},
            "ix": ix or {},
        }

    # ── 身份 / 版本 ──
    def database_name(self):
        return self._db

    def connect_port(self):
        # DSN 派生的宿主端口；刻意不返回 server_internal_port（@@port）
        return self._port

    def current_user(self):
        return self._user

    def alembic_version(self):
        return self._alembic

    def set_alembic_version(self, rev):
        self._alembic = rev

    # ── introspection ──
    def column_exists(self, t, c):
        return c in self.tables[t]["cols"]

    def column_type(self, t, c):
        return self.tables[t]["cols"][c]["type"]

    def column_nullable(self, t, c):
        return self.tables[t]["cols"][c]["nullable"]

    def column_default(self, t, c):
        return self.tables[t]["cols"][c]["default"]

    def fk_exists(self, t, c, fkname, reft):
        return self.tables[t]["fk"].get(c) == (fkname, reft)

    def index_exists(self, t, c, ixname):
        return ixname in self.tables[t]["ix"].get(c, [])

    def row_count(self, t):
        return len(self.tables[t]["rows"])

    def null_count(self, t, c):
        return sum(1 for r in self.tables[t]["rows"] if r.get(c) is None)

    def status_distribution(self, t, c):
        return sorted(Counter(r[c] for r in self.tables[t]["rows"]).items())

    # ── 写入 ──
    def exec_ddl(self, sql):
        self.ops.append(("DDL", sql))
        self._apply_ddl(sql)

    def exec_sql(self, sql):
        self.ops.append(("SQL", sql))
        self._apply_update(sql)

    def _apply_ddl(self, sql):
        if "ADD COLUMN school_id" in sql:
            self.tables["warning_feedback"]["cols"]["school_id"] = {
                "type": "bigint",
                "nullable": True,
                "default": None,
            }
            for r in self.tables["warning_feedback"]["rows"]:
                r.setdefault("school_id", None)
        elif "ADD CONSTRAINT fk_warning_feedback_school_id" in sql:
            self.tables["warning_feedback"]["fk"]["school_id"] = (FK_NAME, "schools")
        elif "ADD INDEX ix_warning_feedback_school_id" in sql:
            self.tables["warning_feedback"]["ix"].setdefault("school_id", []).append(IX_NAME)
        elif "ALTER TABLE ai_runs ALTER COLUMN status SET DEFAULT" in sql:
            # 仅改 default，不碰 type / nullable / 行值
            self.tables["ai_runs"]["cols"]["status"]["default"] = EXPECTED_DEFAULT

    def _apply_update(self, sql):
        if "JOIN risk_warnings rw ON wf.warning_id = rw.id" in sql:
            rw_by_id = {
                r["id"]: r for r in self.tables.get("risk_warnings", {}).get("rows", [])
            }
            for wf in self.tables["warning_feedback"]["rows"]:
                if wf.get("school_id") is None:
                    rw = rw_by_id.get(wf.get("warning_id"))
                    wf["school_id"] = rw["school_id"] if rw is not None else None


def make_ctx(db, tmp_path, approved=True):
    return PromotionContext(db=db, approved=approved, evidence_dir=Path(tmp_path))


def _base_tables(db, with_school_id_absent=True):
    """布置 ai_runs + risk_warnings + warning_feedback（默认 school_id 缺失）。"""
    db.add_table(
        "ai_runs",
        columns=[{"name": "id", "type": "bigint"}, {"name": "status", "type": "enum('PLANNING','EXECUTING','COMPLETED','FAILED','CANCELLED')", "default": None}],
        rows=[{"id": 1, "status": "COMPLETED"}, {"id": 2, "status": "CANCELLED"}],
    )
    db.add_table(
        "risk_warnings",
        columns=[{"name": "id", "type": "bigint"}, {"name": "school_id", "type": "bigint"}],
        rows=[{"id": 5, "school_id": 2}, {"id": 6, "school_id": 3}],
    )
    if with_school_id_absent:
        db.add_table(
            "warning_feedback",
            columns=[{"name": "id", "type": "bigint"}, {"name": "warning_id", "type": "bigint"}],
            rows=[],
        )
    else:
        db.add_table(
            "warning_feedback",
            columns=[
                {"name": "id", "type": "bigint"},
                {"name": "warning_id", "type": "bigint"},
                {"name": "school_id", "type": "bigint", "nullable": True, "default": None},
            ],
            rows=[],
            fk={"school_id": (FK_NAME, "schools")},
            ix={"school_id": [IX_NAME]},
        )


# ═══════════════════════════════════════════════════════════════════════════
# 2210
# ═══════════════════════════════════════════════════════════════════════════
def test_2210_apply_absent_rows0(tmp_path):
    db = FakeDB()
    _base_tables(db)
    ctx = make_ctx(db, tmp_path)
    stage_apply_2210(ctx)
    r = stage_verify_2210(ctx)
    assert r["verified"] is True
    stage_stamp_2210(ctx)
    assert db.alembic_version() == REV_2210


def test_2210_apply_unique_backfill(tmp_path):
    db = FakeDB()
    _base_tables(db)
    db.tables["warning_feedback"]["rows"] = [{"id": 1, "warning_id": 5, "school_id": None}]
    ctx = make_ctx(db, tmp_path)
    stage_apply_2210(ctx)
    assert db.null_count("warning_feedback", "school_id") == 0
    assert db.tables["warning_feedback"]["rows"][0]["school_id"] == 2
    stage_verify_2210(ctx)
    stage_stamp_2210(ctx)
    assert db.alembic_version() == REV_2210


def test_2210_unresolved_gt0_fail(tmp_path):
    db = FakeDB()
    _base_tables(db)
    # warning_id=99 在 risk_warnings 中无匹配 → 回填后仍为 None → unresolved=1
    db.tables["warning_feedback"]["rows"] = [{"id": 1, "warning_id": 99, "school_id": None}]
    ctx = make_ctx(db, tmp_path)
    with pytest.raises(ProdGuardError):
        stage_apply_2210(ctx)


def test_2210_present_equivalent_verified(tmp_path):
    db = FakeDB()
    db.add_table(
        "ai_runs",
        columns=[{"name": "status", "type": "enum('PLANNING','CANCELLED')", "default": None}],
        rows=[],
    )
    db.add_table("risk_warnings", columns=[{"name": "id", "type": "bigint"}], rows=[])
    db.add_table(
        "warning_feedback",
        columns=[
            {"name": "id", "type": "bigint"},
            {"name": "school_id", "type": "bigint", "nullable": True, "default": None},
        ],
        rows=[],
        fk={"school_id": (FK_NAME, "schools")},
        ix={"school_id": [IX_NAME]},
    )
    ctx = make_ctx(db, tmp_path)
    assert decide_2210(db)["decision"] == "VERIFIED"
    stage_apply_2210(ctx)  # no-op
    stage_verify_2210(ctx)
    stage_stamp_2210(ctx)
    assert db.alembic_version() == REV_2210


def test_2210_type_diff_fail(tmp_path):
    db = FakeDB()
    db.add_table("ai_runs", columns=[{"name": "status", "type": "enum('X')", "default": None}], rows=[])
    db.add_table("risk_warnings", columns=[{"name": "id", "type": "bigint"}], rows=[])
    db.add_table(
        "warning_feedback",
        columns=[{"name": "id", "type": "bigint"}, {"name": "school_id", "type": "int", "nullable": True}],
        rows=[],
    )
    ctx = make_ctx(db, tmp_path)
    assert decide_2210(db)["decision"] == "FAIL"
    with pytest.raises(ProdGuardError):
        stage_apply_2210(ctx)


def test_2210_index_missing_fail(tmp_path):
    db = FakeDB()
    db.add_table("ai_runs", columns=[{"name": "status", "type": "enum('X')", "default": None}], rows=[])
    db.add_table("risk_warnings", columns=[{"name": "id", "type": "bigint"}], rows=[])
    db.add_table(
        "warning_feedback",
        columns=[{"name": "id", "type": "bigint"}, {"name": "school_id", "type": "bigint", "nullable": True}],
        rows=[],
        fk={"school_id": (FK_NAME, "schools")},  # FK 在，index 缺
    )
    ctx = make_ctx(db, tmp_path)
    assert decide_2210(db)["decision"] == "FAIL"


def test_2210_head_not_cf04ops_fail(tmp_path):
    db = FakeDB(alembic="other_head")
    _base_tables(db)
    ctx = make_ctx(db, tmp_path)
    with pytest.raises(ProdGuardError):
        stage_apply_2210(ctx)


def test_2210_stamp_denies_without_verify(tmp_path):
    db = FakeDB()  # alembic=cf04ops，过 head 锁，但无 verify evidence
    _base_tables(db)
    ctx = make_ctx(db, tmp_path)
    with pytest.raises(ProdGuardError):
        stage_stamp_2210(ctx)


def test_2210_stamp_head_anomaly(tmp_path):
    db = FakeDB()
    _base_tables(db)
    ctx = make_ctx(db, tmp_path)
    # 伪造一份 verify 通过证据，但把 head 改成异常
    ctx.evidence("verify_2210").write({"checks": {}, "verified": True, "stage": "verify_2210"})
    db.set_alembic_version("other_head")
    with pytest.raises(ProdGuardError):
        stage_stamp_2210(ctx)


# ═══════════════════════════════════════════════════════════════════════════
# 2220
# ═══════════════════════════════════════════════════════════════════════════
def _db_at_2210():
    db = FakeDB(alembic=HEAD_2210)
    db.add_table(
        "ai_runs",
        columns=[{"name": "id", "type": "bigint"}, {"name": "status", "type": "enum('PLANNING','EXECUTING','COMPLETED','FAILED','CANCELLED')", "default": None}],
        rows=[{"id": 1, "status": "COMPLETED"}, {"id": 2, "status": "CANCELLED"}],
    )
    db.add_table("risk_warnings", columns=[{"name": "id", "type": "bigint"}], rows=[])
    db.add_table("warning_feedback", columns=[{"name": "id", "type": "bigint"}], rows=[])
    return db


def test_2220_apply_nine_state_default_none(tmp_path):
    db = _db_at_2210()
    ctx = make_ctx(db, tmp_path)
    stage_apply_2220(ctx)
    assert db.column_default("ai_runs", "status") == EXPECTED_DEFAULT
    r = stage_verify_2220(ctx)
    assert r["verified"] is True
    stage_stamp_2220(ctx)
    assert db.alembic_version() == REV_2220


def test_2220_already_planning_verified(tmp_path):
    db = _db_at_2210()
    db.tables["ai_runs"]["cols"]["status"]["default"] = EXPECTED_DEFAULT
    ctx = make_ctx(db, tmp_path)
    assert decide_2220(db)["decision"] == "VERIFIED"
    stage_apply_2220(ctx)
    stage_verify_2220(ctx)
    stage_stamp_2220(ctx)
    assert db.alembic_version() == REV_2220


def test_2220_enum_changed_fail(tmp_path):
    db = _db_at_2210()
    ctx = make_ctx(db, tmp_path)
    stage_apply_2220(ctx)
    # 模拟“顺手重建 enum”的事故：apply 后 enum 被改
    db.tables["ai_runs"]["cols"]["status"]["type"] = "enum('PLANNING','EXECUTING','COMPLETED','FAILED')"
    with pytest.raises(ProdGuardError):
        stage_verify_2220(ctx)


def test_2220_distribution_changed_fail(tmp_path):
    db = _db_at_2210()
    ctx = make_ctx(db, tmp_path)
    stage_apply_2220(ctx)
    # 模拟数据分布变化
    db.tables["ai_runs"]["rows"][0]["status"] = "FAILED"
    with pytest.raises(ProdGuardError):
        stage_verify_2220(ctx)


def test_2220_head_not_2210_fail(tmp_path):
    db = FakeDB(alembic=HEAD_CF04OPS)  # 未过 2210
    db.add_table("ai_runs", columns=[{"name": "status", "type": "enum('X')", "default": None}], rows=[])
    db.add_table("risk_warnings", columns=[{"name": "id", "type": "bigint"}], rows=[])
    db.add_table("warning_feedback", columns=[{"name": "id", "type": "bigint"}], rows=[])
    ctx = make_ctx(db, tmp_path)
    with pytest.raises(ProdGuardError):
        stage_apply_2220(ctx)


def test_2220_stamp_denies_without_verify(tmp_path):
    db = _db_at_2210()
    ctx = make_ctx(db, tmp_path)
    with pytest.raises(ProdGuardError):
        stage_stamp_2220(ctx)


def test_2220_stamp_head_anomaly(tmp_path):
    db = _db_at_2210()
    ctx = make_ctx(db, tmp_path)
    ctx.evidence("verify_2220").write({"checks": {}, "verified": True, "stage": "verify_2220"})
    db.set_alembic_version(HEAD_CF04OPS)
    with pytest.raises(ProdGuardError):
        stage_stamp_2220(ctx)


# ═══════════════════════════════════════════════════════════════════════════
# PROD GUARD（反向：只准 production）
# ═══════════════════════════════════════════════════════════════════════════
def test_guard_staging_db_fail(tmp_path):
    db = FakeDB(db_name="wings3_staging", port=3308)
    ctx = make_ctx(db, tmp_path)
    with pytest.raises(ProdGuardError):
        require_prod_guard(ctx)


def test_guard_staging_port_fail(tmp_path):
    db = FakeDB(port=3308)
    ctx = make_ctx(db, tmp_path)
    with pytest.raises(ProdGuardError):
        require_prod_guard(ctx)


def test_guard_not_approved_fail(tmp_path):
    db = FakeDB()
    ctx = make_ctx(db, tmp_path, approved=False)
    with pytest.raises(ProdGuardError):
        require_prod_guard(ctx)


def test_guard_wrong_user_fail(tmp_path):
    db = FakeDB(user="root")
    ctx = make_ctx(db, tmp_path)
    with pytest.raises(ProdGuardError):
        require_prod_guard(ctx)


def test_guard_ok(tmp_path):
    db = FakeDB()
    ctx = make_ctx(db, tmp_path)
    g = require_prod_guard(ctx)
    assert g["environment"] == "production"
    assert g["db"] == "wings3"
    assert g["port"] == 3307
    assert g["user"] == "grade7"


# ═══════════════════════════════════════════════════════════════════════════
# 集成：完整 happy path + postflight
# ═══════════════════════════════════════════════════════════════════════════
def test_full_bridge_happy_path(tmp_path):
    db = FakeDB()  # cf04ops
    db.add_table(
        "ai_runs",
        columns=[{"name": "id", "type": "bigint"}, {"name": "status", "type": "enum('PLANNING','EXECUTING','COMPLETED','FAILED','CANCELLED')", "default": None}],
        rows=[{"id": 1, "status": "COMPLETED"}],
    )
    db.add_table(
        "risk_warnings",
        columns=[{"name": "id", "type": "bigint"}, {"name": "school_id", "type": "bigint"}],
        rows=[{"id": 5, "school_id": 2}],
    )
    db.add_table(
        "warning_feedback",
        columns=[{"name": "id", "type": "bigint"}, {"name": "warning_id", "type": "bigint"}],
        rows=[{"id": 1, "warning_id": 5, "school_id": None}],
    )
    ctx = make_ctx(db, tmp_path)

    stage_apply_2210(ctx)
    stage_verify_2210(ctx)
    stage_stamp_2210(ctx)
    assert db.alembic_version() == REV_2210

    stage_apply_2220(ctx)
    stage_verify_2220(ctx)
    stage_stamp_2220(ctx)
    assert db.alembic_version() == REV_2220

    pf = stage_postflight(ctx)
    assert pf["coherent"] is True
    assert pf["alembic_head"] == HEAD_2220


# ═══════════════════════════════════════════════════════════════════════════
# secret_redactor
# ═══════════════════════════════════════════════════════════════════════════
def test_redact_url_masks_password():
    url = "mysql+aiomysql://grade7:s3cr3t@127.0.0.1:3307/wings3?charset=utf8mb4"
    out = redact_url(url)
    assert "s3cr3t" not in out
    assert "***REDACTED***" in out
    assert "grade7" in out
    assert "wings3" in out


def test_redact_dict_masks_sensitive_keys():
    obj = {"DATABASE_URL_SYNC": "mysql://u:p@h/db", "alembic": "cf04ops", "rows": 7}
    out = redact(obj)
    assert out["DATABASE_URL_SYNC"] == "***REDACTED***"
    assert out["alembic"] == "cf04ops"
    assert out["rows"] == 7


def test_redact_text_masks_inline_secret():
    text = "connect with password=topsecret and token=abc"
    out = redact_text(text)
    assert "topsecret" not in out
    assert "abc" not in out
    assert "***REDACTED***" in out


# ═══════════════════════════════════════════════════════════════════════════
# SHA-REVIEW 专项 1：guard 端口必须来自 DSN，不能是 MySQL @@port
# （容器化部署中 MySQL 自报的是容器内部端口，3307→3306 映射时恒为 3306，
#   据此判定会把生产/staging 混判 → guard 误杀。2026-09-07 BREAK-GLASS 已踩过。）
# ═══════════════════════════════════════════════════════════════════════════
def test_guard_uses_dsn_port_not_server_internal_port(tmp_path):
    """DSN port=3307 应放行，即便 MySQL 自报 @@port=3306。"""
    db = FakeDB(port=3307, server_internal_port=3306)
    ctx = make_ctx(db, tmp_path)
    g = require_prod_guard(ctx)
    assert g["port"] == 3307  # 用的是 DSN 宿主端口
    assert db.server_internal_port == 3306  # @@port 存在但未被采信


def test_guard_dsn_3308_fail_even_if_server_port_3306(tmp_path):
    """DSN 指向 staging 3308 → 必须 FAIL，即便 @@port 自报 3306。"""
    db = FakeDB(port=3308, server_internal_port=3306)
    ctx = make_ctx(db, tmp_path)
    with pytest.raises(ProdGuardError):
        require_prod_guard(ctx)


def test_source_never_queries_server_internal_port():
    """源码级钉死：guard 相关模块不得用 SELECT @@port 判定端口。"""
    for name in (
        "prod_promotion_preflight.py",
        "prod_2210_2220_bridge.py",
        "prod_promotion_postflight.py",
    ):
        src = (ROOT / "ops" / "prod_promotion" / name).read_text(encoding="utf-8")
        assert "@@port" not in src, f"{name} 不得使用 SELECT @@port 判定端口"


# ═══════════════════════════════════════════════════════════════════════════
# SHA-REVIEW 专项 2：stamp 必须即时 re-verify，evidence 只是审计记录
# （evidence 被旧文件/错误状态/误写污染时，仍可能盖章 → 必须在盖章前重读 DB）
# ═══════════════════════════════════════════════════════════════════════════
def test_stamp_2210_live_reverify_rejects_drift(tmp_path):
    """evidence 齐全且 VERIFIED，但盖章前物理已漂移 → 拒绝且不动 alembic_version。"""
    db = FakeDB()
    _base_tables(db)
    ctx = make_ctx(db, tmp_path)
    stage_apply_2210(ctx)
    stage_verify_2210(ctx)
    assert ctx.evidence("verify_2210").read()["verified"] is True
    # 漂移：FK 被删（模拟有人在 verify 之后动过库）
    db.tables["warning_feedback"]["fk"].pop("school_id", None)
    with pytest.raises(ProdGuardError, match="即时复核"):
        stage_stamp_2210(ctx)
    assert db.alembic_version() == HEAD_CF04OPS  # 未盖章


def test_stamp_2210_stamps_only_after_live_reverify(tmp_path):
    db = FakeDB()
    _base_tables(db)
    ctx = make_ctx(db, tmp_path)
    stage_apply_2210(ctx)
    stage_verify_2210(ctx)
    r = stage_stamp_2210(ctx)
    assert r["evidence_used_as"] == "AUDIT_ONLY"
    assert r["live_reverify"]["fk"] is True
    assert db.alembic_version() == REV_2210


def test_stamp_2220_live_reverify_rejects_drift(tmp_path):
    db = _db_at_2210()
    ctx = make_ctx(db, tmp_path)
    stage_apply_2220(ctx)
    stage_verify_2220(ctx)
    assert ctx.evidence("verify_2220").read()["verified"] is True
    # 漂移：DEFAULT 被改回 None
    db.tables["ai_runs"]["cols"]["status"]["default"] = None
    with pytest.raises(ProdGuardError, match="即时复核"):
        stage_stamp_2220(ctx)
    assert db.alembic_version() == HEAD_2210  # 未盖章


def test_stamp_2220_stamps_only_after_live_reverify(tmp_path):
    db = _db_at_2210()
    ctx = make_ctx(db, tmp_path)
    stage_apply_2220(ctx)
    stage_verify_2220(ctx)
    r = stage_stamp_2220(ctx)
    assert r["evidence_used_as"] == "AUDIT_ONLY"
    assert r["live_reverify"]["default_planning"] is True
    assert db.alembic_version() == REV_2220
