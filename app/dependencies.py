import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.errors import APIError
from app.models import User

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
    if (
        not settings.api_token
        or not settings.api_token.get_secret_value()
        or not settings.api_user_id
    ):
        raise APIError(503, "AUTH_NOT_CONFIGURED", "Не настроен доступ к API.")
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode(), settings.api_token.get_secret_value().encode()
    ):
        raise APIError(401, "UNAUTHORIZED", "Требуется Bearer-токен.")
    user = session.get(User, settings.api_user_id)
    if user is None or not user.is_active:
        raise APIError(403, "FORBIDDEN", "Пользователь недоступен.")
    if user.role.lower() not in {"hr", "admin", "viewer"}:
        raise APIError(403, "FORBIDDEN", "Нет доступа к вакансиям.")
    return user


UserDep = Annotated[User, Depends(current_user)]


def require_editor(user: User) -> None:
    if user.role.lower() not in {"hr", "admin"}:
        raise APIError(403, "FORBIDDEN", "Изменять вакансии могут HR и администратор.")
