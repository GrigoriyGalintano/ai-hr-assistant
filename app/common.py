from typing import Annotated
from uuid import UUID

from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import APIError
from app.models import (
    Application,
    AuditLog,
    Candidate,
    DeletionRequest,
    Interview,
    OnboardingPlan,
    OnboardingTask,
    Resume,
    Screening,
    User,
    Vacancy,
)
from app.schemas import APIModel, Pagination


class Page[T](APIModel):
    items: list[T]
    pagination: Pagination


class PageParams:
    def __init__(
        self,
        page: Annotated[int, Query(ge=1, le=1000000)] = 1,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        self.page = page
        self.limit = limit


def paginate(session: Session, query, output, params: PageParams):
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = session.scalars(query.offset((params.page - 1) * params.limit).limit(params.limit)).all()
    return Page[output](
        items=[output.model_validate(row) for row in rows],
        pagination=Pagination(
            page=params.page,
            limit=params.limit,
            total=total,
            total_pages=(total + params.limit - 1) // params.limit,
        ),
    )


def owned_query(model, user: User):
    vacancy_ids = select(Vacancy.id).where(Vacancy.organization_id == user.organization_id)
    candidate_ids = select(Candidate.id).where(Candidate.organization_id == user.organization_id)
    application_ids = select(Application.id).where(
        Application.vacancy_id.in_(vacancy_ids), Application.candidate_id.in_(candidate_ids)
    )
    plan_ids = select(OnboardingPlan.id).where(OnboardingPlan.application_id.in_(application_ids))
    if model in (Vacancy, Candidate, User, DeletionRequest):
        condition = model.organization_id == user.organization_id
    elif model is Resume:
        condition = Resume.candidate_id.in_(candidate_ids)
    elif model is Application:
        condition = Application.id.in_(application_ids)
    elif model in (Interview, Screening, OnboardingPlan):
        condition = model.application_id.in_(application_ids)
    elif model is OnboardingTask:
        condition = OnboardingTask.plan_id.in_(plan_ids)
    else:
        raise TypeError(f"Unsupported resource: {model}")
    return select(model).where(condition)


def owned(session: Session, user: User, model, entity_id: UUID, *, lock=False):
    query = owned_query(model, user).where(model.id == entity_id)
    if lock:
        query = query.with_for_update()
    row = session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise APIError(404, "NOT_FOUND", "Объект не найден.")
    return row


def audit_event(session: Session, user: User, entity, action: str):
    session.flush()
    session.add(
        AuditLog(
            organization_id=user.organization_id,
            actor_id=user.id,
            action=action,
            entity_type=entity.__tablename__,
            entity_id=entity.id,
        )
    )


def conflict(message: str):
    raise APIError(409, "STATE_CONFLICT", message)


def require_admin(user: User):
    if user.role.lower() != "admin":
        raise APIError(403, "FORBIDDEN", "Действие доступно администратору.")
