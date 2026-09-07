"""prod_promotion_preflight — 生产硬 guard + promotion 上下文 + 证据存储。

PROD-SEMANTIC-BRIDGE-CANDIDATE-001（LOCAL/GIT ONLY）。
本模块只定义契约、只读断言与生产连接建立；DDL 由 bridge 模块编排。
真实维护窗口执行时才调用 load_production_db()；LOCAL candidate 轮不调用、不连生产。

硬 guard 设计（反向于原 2210/2220 的 staging-only guard）：
  只准 production promotion 语境：DB_NAME=wings3 / external_port=3307 / DB user=grade7。
  任何 staging / 3308 → FAIL。
  WINGS_PROD_PROMOTION=APPROVED 是防误触开关，不替代任何 DB/head/schema 断言。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

# ── 生产硬 guard 常量 ──
PROD_DB_NAME = "wings3"
PROD_PORT = 3307
PROD_USER = "grade7"
PROD_ENVIRONMENT = "production"

# 阶段锁：每个阶段进入前必须匹配的 alembic head
HEAD_CF04OPS = "cf04ops_20260816"
HEAD_2210 = "20260907_2210"
HEAD_2220 = "20260907_2220"

REV_2210 = "20260907_2210"
REV_2220 = "20260907_2220"

APPROVE_ENV = "WINGS_PROD_PROMOTION"
PROD_ENV_PATH = "/opt/wings3/config/production.env"
EVIDENCE_DEFAULT_DIR = Path("/var/log/wings3/prod_promotion")


class ProdGuardError(RuntimeError):
    """guard / head 锁 / 物理语义未验证 等硬拒绝。调用方以非零退出码终止。"""


@runtime_checkable
class ProdDB(Protocol):
    """生产 DB 抽象接口。MysqlProdBridge 为真连接；测试用 FakeDB 实现同接口。"""

    def database_name(self) -> str: ...
    def connect_port(self) -> int: ...
    def current_user(self) -> str: ...
    def alembic_version(self) -> str: ...
    def column_exists(self, table: str, col: str) -> bool: ...
    def column_type(self, table: str, col: str) -> str: ...
    def column_nullable(self, table: str, col: str) -> bool: ...
    def column_default(self, table: str, col: str) -> str | None: ...
    def fk_exists(self, table: str, col: str, fk_name: str, ref_table: str) -> bool: ...
    def index_exists(self, table: str, col: str, ix_name: str) -> bool: ...
    def row_count(self, table: str) -> int: ...
    def null_count(self, table: str, col: str) -> int: ...
    def status_distribution(self, table: str, col: str) -> list[tuple]: ...
    def exec_ddl(self, sql: str) -> None: ...
    def exec_sql(self, sql: str) -> None: ...
    def set_alembic_version(self, rev: str) -> None: ...


class BoundEvidence:
    """把 stage 名绑定到 EvidenceStore，阶段函数直接 .write(payload) / .read()。"""

    def __init__(self, store: "EvidenceStore", stage: str) -> None:
        self._store = store
        self._stage = stage

    def write(self, payload: dict) -> None:
        self._store.write(self._stage, payload)

    def read(self) -> dict | None:
        return self._store.read(self._stage)

    def exists(self) -> bool:
        return self._store.exists(self._stage)


@dataclass
class PromotionContext:
    db: ProdDB
    approved: bool = False
    evidence_dir: Path = EVIDENCE_DEFAULT_DIR
    dry_run: bool = False

    def evidence(self, stage: str) -> BoundEvidence:
        return BoundEvidence(EvidenceStore(self.evidence_dir), stage)


class EvidenceStore:
    """阶段间证据持久化（独立进程调用必须靠它接力）。JSON 落盘，供 stamp 读取 verify 结论。"""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, stage: str) -> Path:
        return self.dir / f"evidence_{stage}.json"

    def write(self, stage: str, payload: dict) -> None:
        self._path(stage).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read(self, stage: str) -> dict | None:
        p = self._path(stage)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def exists(self, stage: str) -> bool:
        return self._path(stage).exists()


def require_approve() -> bool:
    """防误触开关：WINGS_PROD_PROMOTION=APPROVED。不是安全认证，只是显式执行令。"""
    return os.environ.get(APPROVE_ENV, "") == "APPROVED"


def require_prod_guard(ctx: PromotionContext) -> dict:
    """反向 guard：只准 production promotion 语境；staging/3308 → FAIL。

    即便 APPROVED 存在，也必须逐个通过 DB/port/user 断言；开关不替代任何断言。
    """
    db = ctx.db
    name = db.database_name()
    port = db.connect_port()
    user = db.current_user()
    problems: list[str] = []

    if not ctx.approved:
        problems.append(f"{APPROVE_ENV} 未设置（防误触开关缺失）")
    # 反向拒绝 staging（即便 name/port 意外相符也先挡）
    if name == "wings3_staging" or port == 3308:
        problems.append(f"反向 guard 命中 staging 上下文 (db={name}, port={port})")
    if name != PROD_DB_NAME:
        problems.append(f"DB_NAME 期望 {PROD_DB_NAME}，实际 {name}")
    if port != PROD_PORT:
        problems.append(f"external_port 期望 {PROD_PORT}，实际 {port}")
    if user.split("@")[0] != PROD_USER:
        problems.append(f"DB user 期望 {PROD_USER}，实际 {user}")

    if problems:
        raise ProdGuardError("GUARD FAIL: " + "; ".join(problems))

    return {"environment": PROD_ENVIRONMENT, "db": name, "port": port, "user": user}


def require_head(ctx: PromotionContext, expected: str) -> str:
    """阶段锁：当前 alembic head 必须精确等于 expected。"""
    cur = ctx.db.alembic_version()
    if cur != expected:
        raise ProdGuardError(f"HEAD 锁失败: 期望 {expected}，实际 {cur}")
    return cur


def _read_env_value(path: str, key: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def load_production_db() -> ProdDB:
    """建立生产连接（grade7 / 3307 / wings3）。仅本函数内 import pymysql，避免测试环境依赖。

    LOCAL candidate 轮不调用；真实维护窗口执行时才调用，且须先过 require_prod_guard。
    """
    try:
        import pymysql  # lazy import
    except ImportError as exc:  # pragma: no cover - 仅生产 venv 可用
        raise ProdGuardError(f"pymysql 不可用：请在生产 venv 内运行（{exc}）")

    url = _read_env_value(PROD_ENV_PATH, "DATABASE_URL_SYNC") or _read_env_value(
        PROD_ENV_PATH, "DATABASE_URL"
    )
    if not url:
        raise ProdGuardError(f"{PROD_ENV_PATH} 未找到 DATABASE_URL_SYNC / DATABASE_URL")

    # 统一驱动前缀为 pymysql（同步）
    cleaned = url.replace("mysql+aiomysql://", "mysql+pymysql://").replace(
        "mysql+asyncmy://", "mysql+pymysql://"
    )
    parsed = urlparse(cleaned)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 3306
    database = parsed.path.lstrip("/").split("?")[0] or PROD_DB_NAME
    conn = pymysql.connect(
        host=host,
        port=port,
        user=parsed.username or "",
        password=parsed.password or "",
        database=database,
        cursorclass=pymysql.cursors.DictCursor,
    )
    return _MysqlProdBridge(conn)


class _MysqlProdBridge:
    """生产真实连接适配（实现 ProdDB）。LOCAL candidate 轮不实例化。"""

    def __init__(self, conn) -> None:
        self._conn = conn

    # ── 身份 / 版本 ──
    def database_name(self) -> str:
        with self._conn.cursor() as cur:
            cur.execute("SELECT DATABASE()")
            return cur.fetchone()["DATABASE()"]

    def connect_port(self) -> int:
        return int(self._conn.port)

    def current_user(self) -> str:
        with self._conn.cursor() as cur:
            cur.execute("SELECT CURRENT_USER()")
            return cur.fetchone()["CURRENT_USER()"]

    def alembic_version(self) -> str:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT version_num FROM alembic_version ORDER BY version_num DESC LIMIT 1"
            )
            row = cur.fetchone()
            return row["version_num"] if row else ""

    # ── 结构 introspection ──
    def column_exists(self, table: str, col: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                (table, col),
            )
            return cur.fetchone()["c"] > 0

    def column_type(self, table: str, col: str) -> str:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                (table, col),
            )
            row = cur.fetchone()
            return (row["COLUMN_TYPE"] or "") if row else ""

    def column_nullable(self, table: str, col: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT IS_NULLABLE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                (table, col),
            )
            row = cur.fetchone()
            return (row["IS_NULLABLE"] == "YES") if row else False

    def column_default(self, table: str, col: str) -> str | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_DEFAULT FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                (table, col),
            )
            row = cur.fetchone()
            return row["COLUMN_DEFAULT"] if row else None

    def fk_exists(self, table: str, col: str, fk_name: str, ref_table: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s "
                "AND CONSTRAINT_NAME=%s AND REFERENCED_TABLE_NAME=%s",
                (table, col, fk_name, ref_table),
            )
            return cur.fetchone()["c"] > 0

    def index_exists(self, table: str, col: str, ix_name: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s",
                (table, ix_name),
            )
            return cur.fetchone()["c"] > 0

    def row_count(self, table: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS c FROM `{table}`")
            return cur.fetchone()["c"]

    def null_count(self, table: str, col: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS c FROM `{table}` WHERE `{col}` IS NULL")
            return cur.fetchone()["c"]

    def status_distribution(self, table: str, col: str) -> list[tuple]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT `{col}`, COUNT(*) AS c FROM `{table}` "
                f"GROUP BY `{col}` ORDER BY `{col}`"
            )
            return [(r[col], r["c"]) for r in cur.fetchall()]

    # ── 写入 ──
    def exec_ddl(self, sql: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(sql)
        self._conn.commit()

    def exec_sql(self, sql: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(sql)
        self._conn.commit()

    def set_alembic_version(self, rev: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM alembic_version")
            cur.execute("INSERT INTO alembic_version (version_num) VALUES (%s)", (rev,))
        self._conn.commit()
