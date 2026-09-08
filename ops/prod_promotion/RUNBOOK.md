# RUNBOOK — PROD-SEMANTIC-BRIDGE（生产语义桥接）

> 任务：`PROD-SEMANTIC-BRIDGE-CANDIDATE-001`（基于 `3b226bc77cf1771925c5519b02c3136dac674a9c`，LOCAL/GIT ONLY）
> 目标：在不篡改 Alembic 历史（不新增 revision、不修改 2210/2220）的前提下，把旧 production 接回 canonical。
> 工具链：`ops/prod_promotion/`

---

## 0. 一句话职责边界

本工具只负责这一段：

```
cf04ops → physical2210 → verify → stamp2210 → physical2220 → verify → stamp2220
```

之后 `0015` / `2230` 回到 **canonical Alembic 正常逐 revision 执行**：

```
alembic upgrade 20260908_0015   + 硬验
alembic upgrade 20260908_2230   + 硬验
deploy.py activate 3b226bc       （激活 canonical APP）
```

**不要把特殊历史修复和正常 migration 混进同一个程序。**

---

## 1. `stamp` 到底是什么意思（最重要）

> `stamp` **不执行任何 DDL**。
> 它只在「物理 schema 等价」已经通过验证之后，恢复 Alembic 的 bookkeeping（把 `alembic_version` 写成目标 revision）。

我们做的是：**physical semantics first, bookkeeping second**。

所以看到 `stamp 2210` / `stamp 2220` 不要误解成「为了过版本号硬盖章」——
前面必有 `apply`（真实 DDL）+ `verify`（物理语义硬验）两步，stamp 只是把账记平。

### stamp 的授权来自即时复核，不是 evidence

`stamp-2210` / `stamp-2220` 在写 `alembic_version` **之前**，会自己重新读取当前 DB 并即时复核：

```
stamp-2210:
  assert current == cf04ops_20260816
  re-read 2210 physical schema（列 / type / nullable / FK / index / null_count）
  verify exact equivalence
  only then stamp 2210
  re-read alembic_version == 20260907_2210

stamp-2220:
  assert current == 20260907_2210
  re-read ai_runs status type / default / distribution
  verify exact 2220 semantics（ENUM 未变 / 分布未变 / DEFAULT=PLANNING）
  only then stamp 2220
  re-read alembic_version == 20260907_2220
```

> **evidence 文件只能作为审计记录，不能作为授权凭证。**
>
> 否则 evidence 被旧文件、错误状态或误写污染时，仍可能在物理 schema 不等价的情况下盖章。
> 实现上：evidence 缺失 → 视为「审计链路不完整」拒绝；evidence 齐全 → 仍须过即时物理复核才允许盖章。

---

## 2. 生产硬 guard（反向于原 2210/2220 的 staging-only guard）

工具启动即验证（任一不符 → 非零退出，零改动）：

| 项 | 期望 | 说明 |
|---|---|---|
| `environment` | `production` | 由下方三项推导 |
| `DB_NAME` | `wings3` | `SELECT DATABASE()` |
| `external_port` | `3307` | `urlparse(DATABASE_URL).port`（宿主映射端口） |
| `DB user` | `grade7` | 应用身份 |
| `CURRENT_ALEMBIC_HEAD` | 按阶段锁 | 见 §3 |

⚠ **端口来源纪律：必须取自 DSN，绝不取 `SELECT @@port`。**
容器化部署中 MySQL 自报的是容器内部端口（如 `3307→3306/tcp` 映射时恒为 3306），
无法区分 `3307`(生产) 与 `3308`(staging)，据此判定会重演 guard 误杀（2026-09-07 BREAK-GLASS 已踩过）。
实现：`DATABASE_URL → urlparse → host/port/db/user` 一路带到 guard。

反向拒绝：任何 `wings3_staging` / `3308` → **FAIL**（本工具只准 production promotion 语境）。

防误触开关：`WINGS_PROD_PROMOTION=APPROVED` 必须存在。
**注意：这个变量不是安全认证本身，只是防误触开关。** 即便它存在，也不跳过任何 DB / head / schema 断言。

---

## 3. 六阶段状态机（每阶段独立子命令，禁止一个命令跑完整链）

```
P0  preflight      → 打印 guard 结论 + 当前 alembic head
P1  apply-2210     → 真实 DDL：ADD COLUMN + 回填 + 断言 unresolved=0 + FK + index
P2  verify-2210    → 重读物理 schema，断言列/FK/index/type/null=0
P3  stamp-2210     → 仅恢复 bookkeeping（须 P2 evidence 已 VERIFIED）
P4  apply-2220     → 真实 DDL：仅 SET DEFAULT 'PLANNING'（不碰 ENUM）
P5  verify-2220    → 断言 enum 前后逐字节一致 + 分布一致 + DEFAULT=PLANNING
P6  stamp-2220     → 仅恢复 bookkeeping（须 P5 evidence 已 VERIFIED）
```

### 阶段锁（按阶段匹配 alembic head）

| 阶段 | 进入前 `alembic_version` 必须 |
|---|---|
| apply-2210 | `cf04ops_20260816` |
| stamp-2210 | `cf04ops_20260816` 且 P2 已 VERIFIED |
| apply-2220 | `20260907_2210` |
| stamp-2220 | `20260907_2210` 且 P5 已 VERIFIED |

每阶段由「上一步 evidence」（落盘 JSON，位于 `--evidence-dir`）接力，保证跨进程调用也须逐步通过。

---

## 4. 2210 bridge 精确语义（复刻原 20260907_2210）

生产当前：`warning_feedback.school_id = ABSENT`，`warning_feedback rows = 0`。

目标：
1. `ADD COLUMN school_id BIGINT NULL`（声明式，已存在则 skip）
2. 按确定组织关系回填（与原 migration 同语义）：
   ```sql
   UPDATE warning_feedback wf
   JOIN risk_warnings rw ON wf.warning_id = rw.id
   SET wf.school_id = rw.school_id
   WHERE wf.school_id IS NULL
   ```
3. 硬断言 `unresolved = COUNT(*) WHERE school_id IS NULL == 0`，否则 RAISE（禁止默认填值）
4. `ADD CONSTRAINT fk_warning_feedback_school_id FOREIGN KEY (school_id) REFERENCES schools(id)`（无 ondelete）
5. `ADD INDEX ix_warning_feedback_school_id (school_id)`

即便当前 0 行，回填代码与 `unresolved` gate 必须保留——这是可复用、可审计的 production bridge，不是针对今天空表的一次性 SQL。

执行后验证（verify）：列存在 / type BIGINT / nullable YES / FK 正确 / index 正确 / NULL count 0 / row count 不变。
然后 `stamp 20260907_2210`，再读 `alembic_version` 精确等于 `20260907_2210`，否则 STOP。

---

## 5. 2220 bridge 精确语义（复刻原 20260907_2220）

生产当前：`ai_runs.status = 9 态 ENUM`，`DEFAULT = NULL`。

Bridge 只能：`ALTER TABLE ai_runs ALTER COLUMN status SET DEFAULT 'PLANNING'`。**不碰 ENUM / nullable / 行值**。

执行前留证：`COLUMN_TYPE` / `nullable` / `status` 分布 / `DEFAULT`。
执行后要求：`COLUMN_TYPE` 前后逐字节一致 / `nullable` 一致 / 分布一致 / `DEFAULT == PLANNING`。
才 `stamp 20260907_2220`，再读 `alembic_version == 20260907_2220`。

---

## 6. 候选轮（LOCAL/GIT ONLY）已完成的证据

- `pytest tests/test_prod_promotion_bridge.py` → **32 passed**
- 覆盖非 happy path：2210 列缺失/可回填/unresolved>0/已等价/type·FK·index 不符/head 锁；2220 9态+default None/已 PLANNING/ENUM 变/distribution 变/head 锁；stamp 未验证 DENY/head 异常 DENY；PROD GUARD staging/3308/未批准/错误 user FAIL；secret_redactor 脱敏。
- SHA-REVIEW 专项（7）：
  1. guard 采用 DSN port=3307 而非 MySQL 自报端口（@@port=3306 时仍放行）
  2. DSN=3308（staging）→ FAIL，即便自报端口为 3306
  3. 源码级钉死：guard 模块不出现 server-internal 端口查询
  4. `stamp-2210` 在证据齐全且 VERIFIED 的情况下，若物理已漂移（FK 被删）→ 拒绝盖章，`alembic_version` 不变
  5. `stamp-2210` 即时复核通过才盖章（payload 含 `live_reverify` / `evidence_used_as=AUDIT_ONLY`）
  6. `stamp-2220` 同上漂移测试（DEFAULT 被改回 None → 拒绝）
  7. `stamp-2220` 即时复核通过才盖章

**证据口径**：`BRIDGE_TARGETED_TESTS = 32 PASS`；`FULL_SUITE = NOT CLEAN / PRE-EXISTING FAILURES`
（`tests/ai_native/*` 存在既有的 collection/fixture 错误，属环境/历史测试债，登记但不宣称 full suite PASS）。
- `git diff --check` → RC=0（无空白/行尾错误）
- 全部新增文件，0 删除；未改 2210/2220 历史、未改 wrapper、未动 develop/release-stable。

---

## 7. 真实维护窗口执行流程（未来，不在候选轮）

最终 production promotion = 两个维度，在**同一个维护窗口事务**内完成，不是分两天：

```
1.  target SHA 冻结（3b226bc...）
2.  production candidate 离线构建
3.  snapshot（备份 + 基线取证）
4.  停 API + 5 个 Celery/后台写服务（wings3 / beat / celery / celery-reports / worker-*）
5.  bridge 2210：apply → verify → stamp
6.  bridge 2220：apply → verify → stamp
7.  alembic upgrade 20260908_0015  + 硬验
8.  alembic upgrade 20260908_2230  + 硬验
9.  激活 canonical APP（deploy.py activate 3b226bc）
10. 启动所有服务
11. health / version / smoke
12. coherence gate（G1 REMOTE_SHA == G2 RELEASE_MANIFEST == G3 RUNTIME == G4 DB_HEAD）
```

### WINGS_PROD_AUTO 开闸纪律（周主任硬门）

当前 `WINGS_PROD_AUTO` 在生产 `production.env` 中**缺失** → production deploy 默认 DEFERRED。
**保持缺失，不要为"提前准备"改成 1。**

真正维护窗口执行时，若要启用全量生产发布（`deploy-production.yml` 经人工审批后）：
```
开闸 → 单次 deployment → 成功/失败 → 立即重新关闸（WINGS_PROD_AUTO 移回缺失/0）
```
不是长期 `WINGS_PROD_AUTO=1`。

本 bridge 工具自身不依赖 `WINGS_PROD_AUTO`；它只依赖 `WINGS_PROD_PROMOTION=APPROVED` 防误触开关 + 生产硬 guard。
