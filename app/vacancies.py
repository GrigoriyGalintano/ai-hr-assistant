from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response
from sqlalchemy import func, select

from app.common import audit_event, conflict
from app.dependencies import SessionDep, UserDep, require_editor
from app.errors import APIError
from app.models import Application, AuditLog, Vacancy, utcnow
from app.schemas import Pagination, Status, VacancyCreate, VacancyList, VacancyOut, VacancyPatch

router = APIRouter(prefix="/api/v1/vacancies", tags=["vacancies"])
TRANSITIONS = {
    "draft": {"active"},
    "active": {"paused", "closed"},
    "paused": {"active", "closed"},
    "closed": set(),
}


def audit(session, user, vacancy, action):
    session.add(
        AuditLog(
            organization_id=user.organization_id,
            actor_id=user.id,
            action=action,
            entity_type="vacancy",
            entity_id=vacancy.id,
        )
    )


@router.post("", response_model=VacancyOut, status_code=201)
def create_vacancy(body: VacancyCreate, response: Response, session: SessionDep, user: UserDep):
    require_editor(user)
    now = utcnow()
    vacancy = Vacancy(
        **body.model_dump(),
        organization_id=user.organization_id,
        created_by=user.id,
        status="draft",
        source="manual",
        created_at=now,
        updated_at=now,
    )
    session.add(vacancy)
    session.flush()
    audit(session, user, vacancy, "vacancy.created")
    session.commit()
    response.headers["Location"] = f"/api/v1/vacancies/{vacancy.id}"
    return vacancy


@router.get("", response_model=VacancyList)
def list_vacancies(
    session: SessionDep,
    user: UserDep,
    page: Annotated[int, Query(ge=1, le=1000000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Status | None = None,
    search: Annotated[str | None, Query(max_length=255)] = None,
    sort: Literal["createdAt", "updatedAt", "title"] = "createdAt",
    order: Literal["asc", "desc"] = "desc",
):
    filters = [Vacancy.organization_id == user.organization_id]
    if status:
        filters.append(Vacancy.status == status)
    if search and search.strip():
        filters.append(Vacancy.title.icontains(search.strip(), autoescape=True))
    total = session.scalar(select(func.count()).select_from(Vacancy).where(*filters)) or 0
    column = {
        "createdAt": Vacancy.created_at,
        "updatedAt": Vacancy.updated_at,
        "title": Vacancy.title,
    }[sort]
    ordering = column.asc() if order == "asc" else column.desc()
    rows = session.scalars(
        select(Vacancy)
        .where(*filters)
        .order_by(ordering, Vacancy.id.asc())
        .offset((page - 1) * limit)
        .limit(limit)
    ).all()
    return VacancyList(
        items=[VacancyOut.model_validate(row) for row in rows],
        pagination=Pagination(
            page=page, limit=limit, total=total, total_pages=(total + limit - 1) // limit
        ),
    )


def find_vacancy(session, user, vacancy_id, *, lock=False):
    query = select(Vacancy).where(
        Vacancy.id == vacancy_id, Vacancy.organization_id == user.organization_id
    )
    if lock:
        query = query.with_for_update()
    vacancy = session.scalar(query)
    if vacancy is None:
        raise APIError(404, "NOT_FOUND", "Вакансия не найдена.")
    return vacancy


@router.get("/{vacancy_id}", response_model=VacancyOut)
def get_vacancy(vacancy_id: UUID, session: SessionDep, user: UserDep):
    return find_vacancy(session, user, vacancy_id)


@router.patch("/{vacancy_id}", response_model=VacancyOut)
def patch_vacancy(vacancy_id: UUID, body: VacancyPatch, session: SessionDep, user: UserDep):
    require_editor(user)
    vacancy = find_vacancy(session, user, vacancy_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    new_status = changes.get("status", vacancy.status)
    if new_status != vacancy.status and new_status not in TRANSITIONS.get(vacancy.status, set()):
        raise APIError(409, "INVALID_STATUS_TRANSITION", "Недопустимый переход статуса.")
    lower = changes.get("salary_from", vacancy.salary_from)
    upper = changes.get("salary_to", vacancy.salary_to)
    if lower is not None and upper is not None and lower > upper:
        raise APIError(400, "VALIDATION_ERROR", "salaryFrom не должен превышать salaryTo.")
    for field, value in changes.items():
        setattr(vacancy, field, value)
    vacancy.updated_at = utcnow()
    audit(session, user, vacancy, "vacancy.updated")
    session.commit()
    return vacancy


@router.delete("/{vacancy_id}", status_code=204)
def delete_vacancy(vacancy_id: UUID, session: SessionDep, user: UserDep):
    require_editor(user)
    vacancy = find_vacancy(session, user, vacancy_id, lock=True)
    if session.scalar(select(Application.id).where(Application.vacancy_id == vacancy.id).limit(1)):
        conflict("У вакансии есть отклики. Используйте статус closed.")
    audit_event(session, user, vacancy, "vacancy.deleted")
    session.delete(vacancy)
    session.commit()
    return Response(status_code=204)
