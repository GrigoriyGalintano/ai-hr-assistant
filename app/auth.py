import secrets
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, func, select

from app.common import (
    Page,
    PageParams,
    audit_event,
    conflict,
    owned,
    owned_query,
    paginate,
    require_admin,
)
from app.dependencies import SessionDep, UserDep
from app.errors import APIError
from app.hr_schemas import AuthOut, Login, Register, UserCreate, UserOut, UserPatch
from app.models import AuthSession, Organization, User, utcnow
from app.security import DUMMY_PASSWORD_HASH, hash_password, token_digest, verify_password

router = APIRouter(prefix="/api/v1", tags=["auth"])


def issue_session(session, user, hours):
    token = secrets.token_urlsafe(32)
    expiry = utcnow() + timedelta(hours=hours)
    session.add(AuthSession(user_id=user.id, token_hash=token_digest(token), expires_at=expiry))
    session.flush()
    return AuthOut(access_token=token, expires_at=expiry, user=UserOut.model_validate(user))


@router.post("/auth/register", response_model=AuthOut, status_code=201)
def register(body: Register, request: Request, session: SessionDep):
    if not request.app.state.settings.allow_registration:
        raise APIError(403, "REGISTRATION_DISABLED", "Регистрация организаций отключена.")
    organization = Organization(name=body.organization_name)
    session.add(organization)
    session.flush()
    user = User(
        organization_id=organization.id,
        full_name=body.full_name,
        email=str(body.email).lower(),
        role="admin",
        password_hash=hash_password(body.password.get_secret_value()),
    )
    session.add(user)
    session.flush()
    audit_event(session, user, user, "user.registered")
    result = issue_session(session, user, request.app.state.settings.session_hours)
    session.commit()
    return result


@router.post("/auth/login", response_model=AuthOut)
def login(body: Login, request: Request, session: SessionDep):
    user = session.scalar(
        select(User).where(
            User.organization_id == body.organization_id,
            func.lower(User.email) == str(body.email).lower(),
            User.password_hash.is_not(None),
        )
    )
    valid = verify_password(
        body.password.get_secret_value(), user.password_hash if user else DUMMY_PASSWORD_HASH
    )
    if not user or not valid or not user.is_active:
        raise APIError(401, "UNAUTHORIZED", "Неверные данные для входа.")
    # Expired sessions have no further use and contain no bearer token in plaintext.
    session.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user.id, AuthSession.expires_at <= utcnow()
        )
    )
    result = issue_session(session, user, request.app.state.settings.session_hours)
    audit_event(session, user, user, "user.logged_in")
    session.commit()
    return result


@router.post("/auth/logout", status_code=204)
def logout(request: Request, session: SessionDep, user: UserDep):
    record = request.state.auth_session
    if record is None:
        raise APIError(400, "STATIC_TOKEN", "Статический токен отключается в настройках сервера.")
    session.delete(record)
    audit_event(session, user, user, "user.logged_out")
    session.commit()
    return Response(status_code=204)


@router.get("/auth/me", response_model=UserOut)
def me(user: UserDep):
    return user


@router.get("/users", response_model=Page[UserOut], tags=["users"])
def users(session: SessionDep, user: UserDep, page: Annotated[PageParams, Depends()]):
    query = owned_query(User, user).order_by(User.full_name, User.id)
    return paginate(session, query, UserOut, page)


@router.post("/users", response_model=UserOut, status_code=201, tags=["users"])
def create_user(body: UserCreate, session: SessionDep, user: UserDep):
    require_admin(user)
    session.scalar(
        select(Organization).where(Organization.id == user.organization_id).with_for_update()
    )
    if session.scalar(
        select(User.id).where(
            User.organization_id == user.organization_id,
            func.lower(User.email) == str(body.email).lower(),
        )
    ):
        conflict("Пользователь с таким email уже существует в организации.")
    target = User(
        organization_id=user.organization_id,
        full_name=body.full_name,
        email=str(body.email).lower(),
        role=body.role,
        password_hash=hash_password(body.password.get_secret_value()),
    )
    session.add(target)
    audit_event(session, user, target, "user.created")
    session.commit()
    return target


@router.get("/users/{user_id}", response_model=UserOut, tags=["users"])
def get_user(user_id: UUID, session: SessionDep, user: UserDep):
    return owned(session, user, User, user_id)


@router.patch("/users/{user_id}", response_model=UserOut, tags=["users"])
def patch_user(user_id: UUID, body: UserPatch, session: SessionDep, user: UserDep):
    require_admin(user)
    session.scalar(
        select(Organization).where(Organization.id == user.organization_id).with_for_update()
    )
    target = owned(session, user, User, user_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    removing_admin = (
        target.role.lower() == "admin"
        and target.is_active
        and (changes.get("role", target.role) != "admin" or not changes.get("is_active", True))
    )
    if removing_admin:
        admins = session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.organization_id == user.organization_id,
                User.role == "admin",
                User.is_active,
            )
        )
        if admins <= 1:
            conflict("В организации должен остаться активный администратор.")
    password = changes.pop("password", None)
    if password is not None:
        target.password_hash = hash_password(password.get_secret_value())
        session.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
    for name, value in changes.items():
        setattr(target, name, value)
    audit_event(session, user, target, "user.updated")
    session.commit()
    return target
