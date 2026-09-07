#!/usr/bin/env python3
"""E1 STAGING evidence runner — ALEMBIC-RECONCILIATION-STAGING-E1 取证工具.

用途（周主任 A′ 批准链，2026-09-07）：
  在 dev-mgmt-root BREAK-GLASS 窗口内、于服务器 staging 树执行，
  负责 T1 预快照取证 / T5 回归核验 / T6 报告数据。stamp 与 upgrade
  本身由 alembic CLI 逐步执行（见 runbook），本工具只取证与断言。

子命令：
  guard        环境身份硬断言 + 当前 alembic_version（BREAK-GLASS 第一步）
  snapshot     T1 预快照：alembic_version + ai_runs.status 分布 + COLUMN_TYPE
               + warning_feedback 结构/行数
  regression   T5 回归：全部断言一次跑完（只读）
  report       输出 STAGING_E1 报告所需全部字段的原始值

硬断言（guard/regression 内置）：
  DATABASE() == wings3_staging 且 @@port == 3308，任一不符 → 退出码 2（ABORT）

连接：读 /opt/wings3/staging/staging.env 的 DATABASE_URL（mysql+aiomysql://）
  并以 pymysql 同步连接（仅 localhost:3308 的 wings3_staging）。
"""

from __future__ import annotations

import sys
from urllib.parse import urlparse

EXPECTED_DB = "wings3_staging"
EXPECTED_PORT = 3308
STAGING_ENV = "/opt/wings3/staging/staging.env"


def die(msg: str, code: int = 2) -> None:
    print(f"[E1-ABORT] {msg}", file=sys.stderr)
    sys.exit(code)


def load_database_url() -> str:
    try:
        with open(STAGING_ENV, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError as exc:
        die(f"无法读取 {STAGING_ENV}: {exc}")
    die(f"{STAGING_ENV} 中未找到 DATABASE_URL")


def connect():
    try:
        import pymysql
    except ImportError:
        die("pymysql 不可用：请在 staging venv 内运行本脚本 "
            "(如 /opt/wings3/staging/venv/bin/python)，或先只读探测可用解释器")
    url = load_database_url().replace("mysql+aiomysql://", "mysql+pymysql://")
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 3306
    if port != EXPECTED_PORT or host not in ("127.0.0.1", "localhost"):
        die(f"DATABASE_URL 指向非 staging 端点：host={host}, port={port}")
    return pymysql.connect(
        host=host,
        port=port,
        user=parsed.username or "",
        password=parsed.password or "",
        database=parsed.lstrip("/").split("?")[0].split("/")[1]
        if False else parsed.path.lstrip("/").split("?")[0],
        cursorclass=pymysql.cursors.Cursor,
    )


def guard(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT DATABASE(), @@port")
        db, port = cur.fetchone()
        cur.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
        versions = [r[0] for r in cur.fetchall()]
    if db != EXPECTED_DB or int(port) != EXPECTED_PORT:
        die(f"环境身份断言失败: db={db}, port={port} "
            f"(expected {EXPECTED_DB}/{EXPECTED_PORT})")
    print(f"[GUARD] db={db} port={port} OK")
    print(f"[GUARD] alembic_version = {versions}")
    return versions


def snapshot(conn) -> None:
    versions = guard(conn)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_TYPE, COLUMN_DEFAULT, IS_NULLABLE FROM "
            "information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='ai_runs' AND COLUMN_NAME='status'"
        )
        ctype, cdefault, cnull = cur.fetchone() or ("", None, None)
        cur.execute(
            "SELECT status, COUNT(*) FROM ai_runs GROUP BY status ORDER BY status"
        )
        dist = cur.fetchall()
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM "
            "information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='warning_feedback' ORDER BY ORDINAL_POSITION"
        )
        wf_cols = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM warning_feedback")
        wf_rows = cur.fetchone()[0]
    print("[SNAPSHOT] STAGING_ALEMBIC_VERSION_BEFORE =", versions)
    print(f"[SNAPSHOT] ai_runs.status: type={ctype} default={cdefault} "
          f"nullable={cnull}")
    print(f"[SNAPSHOT] ai_runs.status distribution = {dist}")
    print(f"[SNAPSHOT] warning_feedback rows_before = {wf_rows}")
    print(f"[SNAPSHOT] warning_feedback columns = {wf_cols}")


def regression(conn) -> None:
    versions = guard(conn)
    ok = True
    with conn.cursor() as cur:
        # ai_runs.status 三项
        cur.execute(
            "SELECT COLUMN_TYPE, COLUMN_DEFAULT, IS_NULLABLE FROM "
            "information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='ai_runs' AND COLUMN_NAME='status'"
        )
        ctype, cdefault, cnull = cur.fetchone() or ("", None, None)
        cur.execute(
            "SELECT status, COUNT(*) FROM ai_runs GROUP BY status ORDER BY status"
        )
        dist = cur.fetchall()
        # warning_feedback 四项
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE "
            "TABLE_SCHEMA=DATABASE() AND TABLE_NAME='warning_feedback' "
            "AND COLUMN_NAME='school_id'"
        )
        wf_school = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM warning_feedback WHERE school_id IS NULL"
        )
        unresolved = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE WHERE "
            "TABLE_SCHEMA=DATABASE() AND TABLE_NAME='warning_feedback' "
            "AND CONSTRAINT_NAME='fk_warning_feedback_school_id' "
            "AND REFERENCED_TABLE_NAME='schools'"
        )
        fk = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.STATISTICS WHERE "
            "TABLE_SCHEMA=DATABASE() AND TABLE_NAME='warning_feedback' "
            "AND INDEX_NAME='ix_warning_feedback_school_id'"
        )
        ix = cur.fetchone()[0]

    checks = [
        ("ai_runs.status default == PLANNING", cdefault == "PLANNING"),
        ("ai_runs.status nullable == NO（未改变）", cnull == "NO"),
        ("ai_runs.status ENUM 含 8 基线态",
         all(m in ctype for m in
             ("PLANNING", "POLICY_CHECK", "EXECUTING", "WAITING_APPROVAL",
              "RECOVERING", "RESUMING", "COMPLETED", "FAILED"))),
        ("warning_feedback.school_id 列存在", wf_school == 1),
        ("warning_feedback unresolved == 0", unresolved == 0),
        ("warning_feedback FK 存在", fk == 1),
        ("warning_feedback index 存在", ix == 1),
    ]
    for name, passed in checks:
        print(f"[REGRESSION] {'PASS' if passed else 'FAIL'} — {name}")
        ok = ok and passed
    print(f"[REGRESSION] alembic_version = {versions}")
    print(f"[REGRESSION] ai_runs.status distribution = {dist}")
    print(f"[REGRESSION] COLUMN_TYPE = {ctype}, default = {cdefault}")
    if not ok:
        die("regression 存在 FAIL 项（E1=FAIL，停止后续步骤）", 3)
    print("[REGRESSION] ALL PASS")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "guard"
    conn = connect()
    try:
        if cmd == "guard":
            guard(conn)
        elif cmd == "snapshot":
            snapshot(conn)
        elif cmd == "regression":
            regression(conn)
        elif cmd == "report":
            snapshot(conn)
            print("---")
            regression(conn)
        else:
            die(f"未知子命令: {cmd}（guard|snapshot|regression|report）", 1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
