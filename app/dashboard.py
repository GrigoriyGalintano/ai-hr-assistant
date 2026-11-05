from datetime import date

from fastapi import APIRouter
from sqlalchemy import func, select

from app.dependencies import SessionDep, UserDep
from app.models import (
    Application,
    Candidate,
    Interview,
    OnboardingPlan,
    OnboardingTask,
    Screening,
    ScreeningAudit,
    Vacancy,
    utcnow,
)
from app.schemas import APIModel

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


class OnboardingMetrics(APIModel):
    plans: int
    active_plans: int
    tasks: int
    completed_tasks: int
    overdue_tasks: int


class ScreeningMetrics(APIModel):
    total: int
    reviewed: int
    reviews_with_bias: int


class Dashboard(APIModel):
    vacancies_by_status: dict[str, int]
    candidates: int
    applications_by_status: dict[str, int]
    upcoming_interviews: int
    hires: int
    average_time_to_hire_days: float | None
    onboarding: OnboardingMetrics
    screening: ScreeningMetrics


@router.get("", response_model=Dashboard)
def dashboard(session: SessionDep, user: UserDep):
    vacancies = select(Vacancy.id).where(Vacancy.organization_id == user.organization_id)
    candidates = select(Candidate.id).where(Candidate.organization_id == user.organization_id)
    applications = select(Application.id).where(
        Application.vacancy_id.in_(vacancies), Application.candidate_id.in_(candidates)
    )
    plans = select(OnboardingPlan.id).where(OnboardingPlan.application_id.in_(applications))
    active_plans = plans.where(OnboardingPlan.status == "active")
    screenings = select(Screening.id).where(Screening.application_id.in_(applications))

    def count(model, *conditions):
        return session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0

    durations = session.scalar(
        select(
            func.avg(func.extract("epoch", Application.hired_at - Application.applied_at) / 86400)
        ).where(Application.id.in_(applications), Application.hired_at.is_not(None))
    )
    return Dashboard(
        vacancies_by_status=dict(
            session.execute(
                select(Vacancy.status, func.count())
                .where(Vacancy.id.in_(vacancies))
                .group_by(Vacancy.status)
            ).all()
        ),
        candidates=count(Candidate, Candidate.organization_id == user.organization_id),
        applications_by_status=dict(
            session.execute(
                select(Application.status, func.count())
                .where(Application.id.in_(applications))
                .group_by(Application.status)
            ).all()
        ),
        upcoming_interviews=count(
            Interview,
            Interview.application_id.in_(applications),
            Interview.status == "scheduled",
            Interview.scheduled_at >= utcnow(),
        ),
        hires=count(Application, Application.id.in_(applications), Application.status == "hired"),
        average_time_to_hire_days=round(float(durations), 2) if durations is not None else None,
        onboarding=OnboardingMetrics(
            plans=count(OnboardingPlan, OnboardingPlan.id.in_(plans)),
            active_plans=count(OnboardingPlan, OnboardingPlan.id.in_(active_plans)),
            tasks=count(OnboardingTask, OnboardingTask.plan_id.in_(plans)),
            completed_tasks=count(
                OnboardingTask, OnboardingTask.plan_id.in_(plans), OnboardingTask.is_completed
            ),
            overdue_tasks=count(
                OnboardingTask,
                OnboardingTask.plan_id.in_(active_plans),
                ~OnboardingTask.is_completed,
                OnboardingTask.due_date < date.today(),
            ),
        ),
        screening=ScreeningMetrics(
            total=count(Screening, Screening.id.in_(screenings)),
            reviewed=session.scalar(
                select(func.count(func.distinct(ScreeningAudit.screening_id))).where(
                    ScreeningAudit.screening_id.in_(screenings)
                )
            )
            or 0,
            reviews_with_bias=count(
                ScreeningAudit,
                ScreeningAudit.screening_id.in_(screenings),
                ScreeningAudit.bias_detected,
            ),
        ),
    )
