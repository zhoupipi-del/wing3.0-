# E1 Runbook — ALEMBIC-RECONCILIATION-STAGING-E1（A′ 链）

> 批准：周主任 2026-09-07 晚。STAGING WRITE = APPROVED；PRODUCTION WRITE = HOLD。
> 通道：本地备料(Git) → wrapper 部署 E1_CODE_SHA → 一次性 dev-mgmt-root BREAK-GLASS。
> 本 runbook 与两份 migration、evidence script 同 commit，禁止服务器现场修改任何文件。

## 0. 图景修正（E1 前提，BREAK-GLASS 首步验证）

R0 时期"9 heads / 8 orphan"判定来自**服务器旧部署树**的 versions 目录。
Git 真相源（recovery/canonical-from-prod-edd5df8）16 个 revision 是**一条单链**：

```
2d8813121d03 → f7c2a91d4b60 → 20260811_1810 → 0717 → 0806 → 0906
  → 1145 → 1300 → 1400 → 1020 → 1130 → 1200 → a1b2c3 → b2c3d4
  → cf05_20260816 → cf04ops_20260816 → [E1] 20260907_2210 → 20260907_2220
```

DB `alembic_version = ['cf04ops_20260816']` 恰为部署前单链 head ⇒ 部署后图自洽，
**无需 stamp 任何 orphan**。BREAK-GLASS 首步用 `alembic heads` 实机验证：
- heads = 1（`20260907_2220`）且 current = `cf04ops_20260816` → 按本 runbook 继续；
- 出现多 head / unknown revision / 版本表行数 ≠ 1 → **立即停止，退出 root，回报**。

## 1. BREAK-GLASS 第一步：环境身份断言（不是 alembic）

```bash
ssh dev-mgmt-root   # 一次性窗口；全程只允许以下白名单动作
cd /opt/wings3/staging/backend
set -a; . /opt/wings3/staging/staging.env; set +a
# 用 staging venv 的 python（路径以现场只读探测为准，如 /opt/wings3/staging/venv）
<STAGING_PYTHON> ops/e1_staging/e1_evidence.py guard
# 断言失败（db≠wings3_staging 或 port≠3308）→ 立即 exit，不继续
```

## 2. T1 预快照

```bash
<STAGING_PYTHON> ops/e1_staging/e1_evidence.py snapshot | tee /root/e1_snapshot_$(date +%s).log
mysqldump -h127.0.0.1 -P3308 -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" wings3_staging \
  warning_feedback ai_runs alembic_version > /root/e1_pre_snapshot_$(date +%s).sql
```

记录 `STAGING_ALEMBIC_VERSION_BEFORE`。

## 3. 图验证（替代原 T2 六连 stamp）

```bash
<STAGING_ALEMBIC> heads    # 期望：20260907_2220（唯一）
<STAGING_ALEMBIC> current  # 期望：cf04ops_20260816（单行）
```

- 单链确认 → T2 记 `SKIP (6/6): Git 单链图景，无 orphan head 可恢复`；
- 与 §0 期望不符 → 停止，不执行任何 reconciliation。

## 4. T3 — warning_feedback.school_id reconciliation

```bash
<STAGING_ALEMBIC> current                      # before 记录
<STAGING_ALEMBIC> upgrade 20260907_2210
<STAGING_ALEMBIC> current                      # after 记录（=20260907_2210）
```

migration 内置：ENV guard → 声明式 ADD nullable → 组织关系回填 →
unresolved==0 硬断言（违者 RAISE）→ FK/index。
任何 RAISE → 停止，退出 root，回报。

## 5. T4 — ai_runs.status DEFAULT reconciliation

```bash
<STAGING_ALEMBIC> upgrade 20260907_2220        # = upgrade head
<STAGING_ALEMBIC> current                      # after 记录（=20260907_2220）
```

migration 内置：SET DEFAULT 原生 SQL（不触碰 ENUM）→ 前后 COLUMN_TYPE
逐字节比对 + 数据分布比对 + default 断言。任何 RAISE → 停止，回报。

## 6. T5 回归

```bash
<STAGING_PYTHON> ops/e1_staging/e1_evidence.py regression | tee /root/e1_regression_$(date +%s).log
```

## 7. T6 报告 + 退出

汇总 STAGING_E1 验收门字段（见工作区 WINGS_ALEMBIC_RECONCILIATION_DESIGNS.md
§9 与周主任验收门模板），**立即 `exit` 退出 root**，不保留持久权限。

## 禁止清单（全程有效）

production DB/3307、production 树、release/stable、develop、sudoers、用户组、
config 权限、wrapper 修改、服务器现场编辑 migration、`stamp head`、merge
revision、academic_year reconciliation、一次 stamp 多个 revision。
