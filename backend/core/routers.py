"""
core/routers.py — Wings 3.0 核心路由

提供认证、租户管理、组织架构查询等系统级 API。
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User, UserRole
from .schemas import (
    ChangePasswordRequest,
    GradeOut,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    PaginatedResponse,
    SchoolCreate,
    SchoolOut,
    UserOut,
)
from .services import AuthService, OrgService
from .tenant_context import TenantContext, build_tenant_context

router = APIRouter(prefix="/api/v1", tags=["core"])
security = HTTPBearer(auto_error=False)  # 非强制 → 允许 Cookie 降级


# ═══════════════════════════════════════════════════════════════
# 依赖注入
# ═══════════════════════════════════════════════════════════════


async def get_db() -> AsyncSession:
    """获取数据库会话 — 由 app.py 的依赖覆盖实现"""
    raise NotImplementedError("DB session must be injected by app.py")


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """双模雷达：Authorization Header → Cookie access_token 降级"""
    token: str | None = None

    # 模式 A: Authorization: Bearer <token>（原生调用 / Swagger）
    if credentials and credentials.credentials:
        token = credentials.credentials
    # 模式 B: Cookie access_token（Nginx 代理的旧 Flask 前端跨域请求）
    else:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(status_code=401, detail="未提供认证令牌")

    try:
        payload = AuthService.decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")

    # 兼容双套 Payload: Wings 3.0 用 "sub"（str），旧 Flask 用 "user_id"（int）
    raw_id = payload.get("sub") or payload.get("user_id")
    if not raw_id:
        raise HTTPException(status_code=401, detail="令牌缺少用户标识")

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(User)
        .options(selectinload(User.school))
        .where(User.id == int(raw_id), User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")

    return user


def require_role(*roles: UserRole):
    """角色守卫工厂 — 确保当前用户拥有指定角色之一"""

    async def _guard(current_user: User = Depends(get_current_user)):
        user_role = current_user.role
        if isinstance(user_role, str):
            user_role = UserRole(user_role)
        if user_role not in roles:
            raise HTTPException(status_code=403, detail="无权访问此资源")
        return current_user

    return _guard


def require_school_phase(allowed_phases: list[str]):
    """
    学段铁闸工厂 — 确保当前用户所属学校的 school_phase 在允许列表中。

    用法:
        @router.get("/primary-only",
            dependencies=[Depends(require_school_phase(["primary"]))])
        async def primary_only():
            ...

        @router.get("/senior-only",
            dependencies=[Depends(require_school_phase(["senior", "integrated"]))])
        async def senior_only():
            ...

    规则:
        - MS_ADMIN: 放行（超管不受学段限制）
        - 当前用户未关联学校 → 401
        - school_phase 不在 allowed_phases 中 → 403
    """

    async def _phase_guard(current_user: User = Depends(get_current_user)):
        # 超管放行
        user_role = current_user.role
        if isinstance(user_role, str):
            user_role = UserRole(user_role)
        if user_role == UserRole.MS_ADMIN:
            # 超管: 返回其学校的 phase (如果有), 否则 None
            if current_user.school and current_user.school.school_phase:
                return current_user.school.school_phase
            return "junior"  # 兜底

        # 检查学校关联（get_current_user 已 eager-load school）
        if not current_user.school:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="当前账户未关联任何学校，无法判定学段",
            )

        current_phase = current_user.school.school_phase
        if current_phase not in allowed_phases:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"访问受限：当前功能仅对 "
                    f"[{', '.join(allowed_phases)}] 学段开放，"
                    f"您的学校（{current_user.school.name}）属于 "
                    f"[{current_phase}] 学段。"
                ),
            )
        return current_phase

    return _phase_guard


# ═══════════════════════════════════════════════════════════════
# 多租户访问守卫 — P0 安全修复 (2026-06-30)
# ═══════════════════════════════════════════════════════════════


def verify_school_access(requested_school_id: int, current_user: User) -> int:
    """
    校验当前用户是否有权访问 requested_school_id 的数据（同步函数，端点内调用）。

    - MS_ADMIN / GROUP_ADMIN / BRANCH_ADMIN: 放行（超管/集团管理员/片区管理员）
    - 其他角色: requested_school_id 必须等于 current_user.school_id

    用法:
        verify_school_access(school_id, current_user)  # 抛 403 或直接返回 school_id
    """
    user_role = current_user.role
    if isinstance(user_role, str):
        user_role = UserRole(user_role)

    bypass_roles = {UserRole.MS_ADMIN, UserRole.GROUP_ADMIN, UserRole.BRANCH_ADMIN}
    if user_role in bypass_roles:
        return requested_school_id  # 超管/集团/片区放行

    if current_user.school_id != requested_school_id:
        raise HTTPException(
            status_code=403,
            detail="无权访问其他学校的数据",
        )
    return requested_school_id


async def verify_entity_ownership(
    db: AsyncSession,
    model_class: Any,
    entity_id: int,
    current_user: User,
    not_found_msg: str = "资源不存在",
) -> Any:
    """
    实体归属权校验 — 确保 entity_id 对应的记录属于 current_user 的权限范围。

    三层防护:
    1. 实体不存在 → 404（而非 403，避免信息泄露）
    2. 实体 school_id 不在用户权限范围 + 非超管 → 403
    3. 实体无 school_id 列（如系统级表）→ 放行

    角色放行规则（不可妥协原则 — 18/18 PASS 基线不变）:
    - MS_ADMIN: 全局放行（所有学校，等同现有逻辑）
    - GROUP_ADMIN: 放行（其 org 下所有学校，scope 由 access_scope 控制）
    - BRANCH_ADMIN: 放行（其 branch 下所有学校，scope 由 access_scope 控制）
    - 其他角色: 严格 school_id 硬匹配（逻辑不变）

    用法:
        record = await verify_entity_ownership(
            db, DisciplineSanction, 42, current_user, "处分记录不存在"
        )
    """
    from sqlalchemy import select

    result = await db.execute(select(model_class).where(model_class.id == entity_id))
    entity = result.scalar_one_or_none()

    if not entity:
        raise HTTPException(status_code=404, detail=not_found_msg)

    user_role = current_user.role
    if isinstance(user_role, str):
        user_role = UserRole(user_role)

    # 超管角色放行 — MS_ADMIN(全局) / GROUP_ADMIN(集团范围) / BRANCH_ADMIN(片区范围)
    # scope 过滤由 access_scope + build_scope_filter 在查询层实现，此函数只做实体级校验
    bypass_roles = {UserRole.MS_ADMIN, UserRole.GROUP_ADMIN, UserRole.BRANCH_ADMIN}
    if user_role in bypass_roles:
        return entity

    # 只对有 school_id 列的实体做隔离校验（单校角色严格硬匹配）
    if hasattr(entity, "school_id"):
        if entity.school_id != current_user.school_id:
            raise HTTPException(status_code=403, detail="无权访问其他学校的数据")

    return entity


# ═══════════════════════════════════════════════════════════════
# TenantContext 依赖注入 — 三级架构 AccessScope + 级联配置
# ═══════════════════════════════════════════════════════════════


async def get_tenant_context(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    """
    TenantContext 依赖注入 — 自动计算 access_scope + 级联配置。

    使用方式（各模块 router 直接导入）:
        from core.routers import get_tenant_context

        @router.get("/...")
        async def endpoint(ctx: TenantContext = Depends(get_tenant_context)):
            # ctx.access_scope → 用户可访问的 school_ids 列表
            # ctx.is_single_school() → 单校角色
            # ctx.is_cross_school() → 跨校角色
            # await ctx.get_config("attendance") → 级联配置查找
            query = select(Student).where(
                build_scope_filter(Student, ctx.access_scope)
            )

    向下兼容:
        - MS_ADMIN/单校角色: access_scope = [user.school_id]（1个ID，等价硬匹配）
        - GROUP_ADMIN: access_scope = 集团所有 school_ids
        - BRANCH_ADMIN: access_scope = 片区所有 school_ids
    """
    return await build_tenant_context(current_user, db)


# ═══════════════════════════════════════════════════════════════
# 健康检查
# ═══════════════════════════════════════════════════════════════


@router.get("/health", response_model=MessageResponse)
async def health_check():
    return MessageResponse(message="ok", detail="Wings 3.0 Core Online")


@router.get("/version")
async def version_info(request: Request, db: AsyncSession = Depends(get_db)):
    """发布版本指纹（可确认性基础设施）

    用途：确认线上实际运行的是哪一版代码，不再靠日志/目录/猜测判断。

    验收口诀：
        GitHub 最新 commit == 本接口 commit  →  确实已部署
        GitHub 最新 commit != 本接口 commit  →  没部署上去

    version.json 由 CI 在构建阶段生成（含 commit_sha / build_time），
    随代码一起部署；若缺失则回退 unknown，不影响服务可用性。
    """
    import json
    import os
    from pathlib import Path

    from sqlalchemy import select, text

    meta: dict = {}
    vpath = Path(__file__).resolve().parent.parent / "version.json"
    try:
        if vpath.is_file():
            meta = json.loads(vpath.read_text(encoding="utf-8"))
    except Exception:
        meta = {}

    db_revision = "unknown"
    try:
        row = (await db.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))).first()
        if row:
            db_revision = row[0]
    except Exception:
        pass

    payload: dict = {
        "app": "Wings 3.0",
        "environment": os.getenv("ENV") or os.getenv("APP_ENV") or "unknown",
        "commit": meta.get("commit", "unknown"),
        "build_time": meta.get("build_time", "unknown"),
        "db_revision": db_revision,
    }

    # 管理员增强：附加 release.json 明细（backend_sha / frontend_sha / release_tag / released_at）
    # 兼容服务器上原管理员版 /version 的能力，避免本次合并导致功能丢失。
    # 认证为 best-effort：无 token 或非管理员只返回公开指纹，不报错。
    ADMIN_ROLES = {"ms_admin", "school_admin", "group_admin", "branch_admin"}
    try:
        token: str | None = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1]
        else:
            token = request.cookies.get("access_token")

        current_user = None
        if token:
            claims = AuthService.decode_token(token)
            raw_id = claims.get("sub") or claims.get("user_id")
            if raw_id:
                res = await db.execute(
                    select(User).where(User.id == int(raw_id), User.is_active.is_(True))
                )
                current_user = res.scalar_one_or_none()

        role = getattr(current_user, "role", None)
        role_value = getattr(role, "value", role)
        if role_value in ADMIN_ROLES or str(role_value).lower() in ADMIN_ROLES:
            release_json = Path(__file__).resolve().parent.parent.parent / "release.json"
            if release_json.exists():
                payload.update(json.loads(release_json.read_text(encoding="utf-8")))
    except Exception:
        # 增强信息属可选项，失败一律降级为公开指纹
        pass

    return payload


# ═══════════════════════════════════════════════════════════════
# 认证
# ═══════════════════════════════════════════════════════════════


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user, error = await AuthService.authenticate(db, body.username, body.password)
    if error:
        raise HTTPException(status_code=401, detail=error)

    token = AuthService.create_token(user)
    user_out = await AuthService.get_user_out(db, user)

    return LoginResponse(
        access_token=token,
        user=user_out,
        password_change_required=bool(user.password_change_required),
    )


@router.post("/auth/change-password", response_model=MessageResponse)
async def change_password(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改当前用户密码（需已登录）"""
    success, error = await AuthService.change_password(
        db, current_user, body.old_password, body.new_password
    )
    if not success:
        raise HTTPException(status_code=400, detail=error)

    return MessageResponse(message="密码修改成功", detail="请使用新密码重新登录")


@router.get("/auth/me", response_model=UserOut)
async def get_me(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await AuthService.get_user_out(db, current_user)


# ═══════════════════════════════════════════════════════════════
# 学校（租户）管理 — 仅德育处管理员
# ═══════════════════════════════════════════════════════════════


@router.post("/schools", response_model=SchoolOut, status_code=201)
async def create_school(
    body: SchoolCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.MS_ADMIN)),
):
    school = await OrgService.create_school(db, body.name)
    return SchoolOut.model_validate(school)


@router.get("/schools/{school_id}", response_model=SchoolOut)
async def get_school(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取学校详情 — P0 修复: school_id 经 verify_school_access 校验"""
    verify_school_access(school_id, current_user)
    school = await OrgService.get_school(db, school_id)
    if not school:
        raise HTTPException(status_code=404, detail="学校不存在")
    return SchoolOut.model_validate(school)


# ═══════════════════════════════════════════════════════════════
# 模块管理
# ═══════════════════════════════════════════════════════════════


@router.get("/schools/{school_id}/modules")
async def get_school_modules(
    school_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取某学校的所有模块状态 — P0 修复: school_id 经 verify_school_access 校验"""
    verify_school_access(school_id, current_user)
    from sqlalchemy import select

    from .models import SchoolModule

    result = await db.execute(select(SchoolModule).where(SchoolModule.school_id == school_id))
    modules = result.scalars().all()
    return [
        {
            "module_code": m.module_code,
            "enabled": m.enabled,
            "config": m.config,
            "enabled_at": m.enabled_at.isoformat() if m.enabled_at else None,
            "disabled_at": m.disabled_at.isoformat() if m.disabled_at else None,
        }
        for m in modules
    ]


# ═══════════════════════════════════════════════════════════════
# 年级
# ═══════════════════════════════════════════════════════════════


@router.get("/grades", response_model=list[GradeOut])
async def list_grades(
    school_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取年级列表。
    - MS_ADMIN 可传 school_id 查询其他学校
    - 其他角色强制使用 current_user.school_id（school_id 参数被忽略）
    """
    user_role = current_user.role
    if isinstance(user_role, str):
        user_role = UserRole(user_role)

    if user_role == UserRole.MS_ADMIN and school_id is not None:
        sid = school_id  # 超管显式指定学校
    else:
        sid = current_user.school_id  # 其他角色 / 超管未指定: 使用自己的学校

    grades = await OrgService.get_grades(db, sid)
    return [GradeOut.model_validate(g) for g in grades]


# ═══════════════════════════════════════════════════════════════
# 班级
# ═══════════════════════════════════════════════════════════════


@router.get("/classes")
async def list_classes(
    school_id: int | None = None,
    grade_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取班级列表。
    - MS_ADMIN 可传 school_id 查询其他学校
    - 其他角色强制使用 current_user.school_id（school_id 参数被忽略）
    """
    user_role = current_user.role
    if isinstance(user_role, str):
        user_role = UserRole(user_role)

    if user_role == UserRole.MS_ADMIN and school_id is not None:
        sid = school_id
    else:
        sid = current_user.school_id

    classes = await OrgService.get_classes_with_details(db, sid, grade_id)
    return {"total": len(classes), "items": classes}


# ═══════════════════════════════════════════════════════════════
# 学生
# ═══════════════════════════════════════════════════════════════


@router.get("/students", response_model=PaginatedResponse)
async def list_students(
    school_id: int | None = None,
    class_id: int | None = None,
    grade_id: int | None = None,
    gender: str | None = None,
    is_active: bool | None = True,
    search: str | None = None,
    page: int = 1,
    per_page: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    分页查询学生列表。
    - MS_ADMIN 可传 school_id 查询其他学校
    - 其他角色强制使用 current_user.school_id（school_id 参数被忽略）
    """
    user_role = current_user.role
    if isinstance(user_role, str):
        user_role = UserRole(user_role)

    if user_role == UserRole.MS_ADMIN and school_id is not None:
        sid = school_id
    else:
        sid = current_user.school_id

    offset = (page - 1) * per_page

    students, total = await OrgService.get_students_with_names(
        db,
        sid,
        class_id=class_id,
        grade_id=grade_id,
        gender=gender,
        is_active=is_active,
        search=search,
        limit=per_page,
        offset=offset,
    )

    pages = (total + per_page - 1) // per_page if total > 0 else 0
    return PaginatedResponse(
        items=students,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )


# ═══════════════════════════════════════════════════════════════
# 学段隔离铁闸 — 测试端点（验证 require_school_phase 拦截）
# ═══════════════════════════════════════════════════════════════


@router.get("/test/primary-only", dependencies=[Depends(require_school_phase(["primary"]))])
async def test_primary_only():
    """小学专属通道 — 只有 school_phase=primary 的学校可访问"""
    return {
        "status": "success",
        "msg": "成功进入小学萌卡系统底层通道",
    }


@router.get(
    "/test/senior-only", dependencies=[Depends(require_school_phase(["senior", "integrated"]))]
)
async def test_senior_only():
    """高中专属通道 — 只有 school_phase=senior 或 integrated 的学校可访问"""
    return {
        "status": "success",
        "msg": "成功进入高中硬核高考学情大盘",
    }
