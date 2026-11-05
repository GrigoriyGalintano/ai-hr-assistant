import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import APIError
from app.models import AuthSession, User, utcnow
from app.security import token_digest

bearer = HTTPBearer(auto_error=False)


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    settings = request.app.state.settings
    if credentials is None:
        raise APIError(401, "UNAUTHORIZED", "Требуется Bearer-токен.")
    request.state.auth_session = None
    if (
        settings.api_token
        and settings.api_user_id
        and secrets.compare_digest(
            credentials.credentials.encode(), settings.api_token.get_secret_value().encode()
        )
    ):
        user = session.get(User, settings.api_user_id)
    else:
        record = session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == token_digest(credentials.credentials),
                AuthSession.expires_at > utcnow(),
            )
        )
        if record is None:
            raise APIError(401, "UNAUTHORIZED", "Токен недействителен или истёк.")
        user = session.get(User, record.user_id)
        request.state.auth_session = record
    if user is None or not user.is_active:
        raise APIError(403, "FORBIDDEN", "Пользователь недоступен.")
    if user.role.lower() not in {"hr", "admin", "viewer"}:
        raise APIError(403, "FORBIDDEN", "Нет доступа к API.")
    return user


UserDep = Annotated[User, Depends(current_user)]


def require_editor(user: User) -> None:
    if user.role.lower() not in {"hr", "admin"}:
        raise APIError(403, "FORBIDDEN", "Изменения доступны HR и администратору.")
