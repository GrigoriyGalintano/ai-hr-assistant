from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text

from app.common import Page, PageParams, audit_event, conflict, owned, owned_query, paginate
from app.dependencies import SessionDep, UserDep, require_editor
from app.errors import APIError
from app.hr_schemas import (
    ApplicationCreate,
    ApplicationOut,
    ApplicationPatch,
    ApplicationStatus,
    InterviewCreate,
    InterviewOut,
    InterviewPatch,
    InterviewStatus,
)
from app.models import (
    Application,
    Candidate,
    Interview,
    Resume,
    Screening,
    ScreeningAudit,
    User,
    Vacancy,
    utcnow,
)

router = APIRouter(prefix="/api/v1", tags=["applications"])
APPLICATION_TRANSITIONS = {
    "new": {"screening", "interview", "rejected", "withdrawn"},
    "screening": {"interview", "rejected", "withdrawn"},
    "interview": {"offer", "rejected", "withdrawn"},
    "offer": {"hired", "rejected", "withdrawn"},
    "hired": set(),
    "rejected": set(),
    "withdrawn": set(),
}
TERMINAL_APPLICATIONS = {"hired", "rejected", "withdrawn"}


def open_application(row):
    if row.status in TERMINAL_APPLICATIONS:
        conflict("Отклик уже завершён.")


def check_human_review(session, application):
    screening = session.scalar(
        select(Screening)
        .where(Screening.application_id == application.id)
        .order_by(Screening.screened_at.desc(), Screening.id)
        .limit(1)
    )
    if screening is None:
        return
    review = session.scalar(
        select(ScreeningAudit)
        .where(ScreeningAudit.screening_id == screening.id)
        .order_by(ScreeningAudit.reviewed_at.desc(), ScreeningAudit.id)
        .limit(1)
    )
    if review is None or review.bias_detected:
        conflict(
            "Перед оффером и наймом нужна проверка последней AI-оценки без признаков предвзятости."
        )


@router.post("/applications", response_model=ApplicationOut, status_code=201)
def create_application(body: ApplicationCreate, session: SessionDep, user: UserDep):
    require_editor(user)
    vacancy = owned(session, user, Vacancy, body.vacancy_id, lock=True)
    candidate = owned(session, user, Candidate, body.candidate_id, lock=True)
    resume = owned(session, user, Resume, body.resume_id, lock=True)
    if vacancy.status != "active":
        conflict("Принимать отклики можно только по активной вакансии.")
    if resume.candidate_id != candidate.id:
        raise APIError(400, "RESUME_OWNER", "Резюме принадлежит другому кандидату.")
    if session.scalar(
        select(Application.id)
        .where(
            Application.vacancy_id == vacancy.id,
            Application.candidate_id == candidate.id,
            Application.resume_id == resume.id,
        )
        .limit(1)
    ):
        conflict("Этот отклик уже зарегистрирован.")
    row = Application(**body.model_dump(), status="new")
    session.add(row)
    audit_event(session, user, row, "application.created")
    session.commit()
    return row


@router.get("/applications", response_model=Page[ApplicationOut])
def applications(
    session: SessionDep,
    user: UserDep,
    page: Annotated[PageParams, Depends()],
    vacancy_id: Annotated[UUID | None, Query(alias="vacancyId")] = None,
    candidate_id: Annotated[UUID | None, Query(alias="candidateId")] = None,
    status: ApplicationStatus | None = None,
):
    query = owned_query(Application, user)
    if vacancy_id:
        query = query.where(Application.vacancy_id == vacancy_id)
    if candidate_id:
        query = query.where(Application.candidate_id == candidate_id)
    if status:
        query = query.where(Application.status == status)
    return paginate(
        session, query.order_by(Application.applied_at.desc(), Application.id), ApplicationOut, page
    )


@router.get("/applications/{application_id}", response_model=ApplicationOut)
def application(application_id: UUID, session: SessionDep, user: UserDep):
    return owned(session, user, Application, application_id)


@router.patch("/applications/{application_id}", response_model=ApplicationOut)
def patch_application(
    application_id: UUID, body: ApplicationPatch, session: SessionDep, user: UserDep
):
    require_editor(user)
    row = owned(session, user, Application, application_id, lock=True)
    if body.status != row.status:
        if body.status not in APPLICATION_TRANSITIONS.get(row.status, set()):
            conflict("Недопустимый переход статуса отклика.")
        if body.status in {"offer", "hired"}:
            check_human_review(session, row)
        if body.status == "hired":
            row.hired_at = utcnow()
        row.status = body.status
        if body.status in TERMINAL_APPLICATIONS:
            for meeting in session.scalars(
                select(Interview)
                .where(
                    Interview.application_id == row.id,
                    Interview.status == "scheduled",
                )
                .with_for_update()
            ):
                meeting.status = "cancelled"
        audit_event(session, user, row, "application.updated")
        session.commit()
    return row


def validate_slot(session, user, interviewer_id, starts, duration, *, exclude_id=None):
    interviewer = owned(session, user, User, interviewer_id, lock=True)
    if not interviewer.is_active or interviewer.role.lower() not in {"hr", "admin"}:
        raise APIError(
            400,
            "INTERVIEWER_UNAVAILABLE",
            "Интервьюер должен быть активным HR или администратором.",
        )
    try:
        ends = starts + timedelta(minutes=duration)
    except OverflowError:
        raise APIError(
            400, "INVALID_TIME", "Дата интервью выходит за допустимый диапазон."
        ) from None
    query = select(Interview.id).where(
        Interview.interviewer_id == interviewer.id,
        Interview.status == "scheduled",
        Interview.scheduled_at < ends,
        Interview.scheduled_at + Interview.duration_minutes * text("INTERVAL '1 minute'") > starts,
    )
    if exclude_id:
        query = query.where(Interview.id != exclude_id)
    if session.scalar(query.limit(1)):
        conflict("У интервьюера уже есть встреча в это время.")


@router.post(
    "/applications/{application_id}/interviews",
    response_model=InterviewOut,
    status_code=201,
    tags=["interviews"],
)
def create_interview(
    application_id: UUID, body: InterviewCreate, session: SessionDep, user: UserDep
):
    require_editor(user)
    row = owned(session, user, Application, application_id, lock=True)
    open_application(row)
    if body.scheduled_at <= utcnow():
        raise APIError(400, "INVALID_TIME", "Назначьте интервью на будущее время.")
    validate_slot(session, user, body.interviewer_id, body.scheduled_at, body.duration_minutes)
    meeting = Interview(**body.model_dump(), application_id=row.id, status="scheduled")
    session.add(meeting)
    if row.status in {"new", "screening"}:
        row.status = "interview"
        audit_event(session, user, row, "application.updated")
    audit_event(session, user, meeting, "interview.created")
    session.commit()
    return meeting


@router.get("/interviews", response_model=Page[InterviewOut], tags=["interviews"])
def interviews(
    session: SessionDep,
    user: UserDep,
    page: Annotated[PageParams, Depends()],
    application_id: Annotated[UUID | None, Query(alias="applicationId")] = None,
    interviewer_id: Annotated[UUID | None, Query(alias="interviewerId")] = None,
    status: InterviewStatus | None = None,
):
    query = owned_query(Interview, user)
    if application_id:
        query = query.where(Interview.application_id == application_id)
    if interviewer_id:
        query = query.where(Interview.interviewer_id == interviewer_id)
    if status:
        query = query.where(Interview.status == status)
    return paginate(
        session, query.order_by(Interview.scheduled_at, Interview.id), InterviewOut, page
    )


@router.get("/interviews/{interview_id}", response_model=InterviewOut, tags=["interviews"])
def interview(interview_id: UUID, session: SessionDep, user: UserDep):
    return owned(session, user, Interview, interview_id)


@router.patch("/interviews/{interview_id}", response_model=InterviewOut, tags=["interviews"])
def patch_interview(interview_id: UUID, body: InterviewPatch, session: SessionDep, user: UserDep):
    require_editor(user)
    meeting = owned(session, user, Interview, interview_id)
    row = owned(session, user, Application, meeting.application_id, lock=True)
    meeting = owned(session, user, Interview, interview_id, lock=True)
    if meeting.status != "scheduled":
        conflict("Завершённое или отменённое интервью нельзя изменять.")
    open_application(row)
    changes = body.model_dump(exclude_unset=True)
    status = changes.get("status", meeting.status)
    result = changes.get("result", meeting.result)
    if status == "completed" and result is None:
        raise APIError(400, "RESULT_REQUIRED", "Укажите результат завершённого интервью.")
    if status != "completed" and result is not None:
        raise APIError(400, "INVALID_RESULT", "Результат указывается при завершении интервью.")
    starts = changes.get("scheduled_at", meeting.scheduled_at)
    duration = changes.get("duration_minutes", meeting.duration_minutes)
    if status == "scheduled":
        if "scheduled_at" in changes and starts <= utcnow():
            raise APIError(400, "INVALID_TIME", "Назначьте интервью на будущее время.")
        validate_slot(
            session,
            user,
            changes.get("interviewer_id", meeting.interviewer_id),
            starts,
            duration,
            exclude_id=meeting.id,
        )
    for name, value in changes.items():
        setattr(meeting, name, value)
    audit_event(session, user, meeting, "interview.updated")
    session.commit()
    return meeting
