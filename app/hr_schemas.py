from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    EmailStr,
    Field,
    SecretStr,
    StrictBool,
    StringConstraints,
    field_serializer,
    model_validator,
)

from app.schemas import APIModel, Body, Title

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=30)]
Score = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]
Role = Literal["hr", "admin", "viewer"]
ApplicationStatus = Literal[
    "new", "screening", "interview", "offer", "hired", "rejected", "withdrawn"
]
InterviewStatus = Literal["scheduled", "completed", "cancelled"]
InterviewResult = Literal["passed", "failed", "inconclusive"]
PlanStatus = Literal["draft", "active", "completed", "cancelled"]
Provider = Literal["hh", "superjob"]
ExternalID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,100}$")]
Password = Annotated[SecretStr, Field(min_length=12, max_length=128)]
StartDate = Annotated[date, Field(le=date.max - timedelta(days=90))]


class ResourceOut(APIModel):
    @field_serializer(
        "created_at",
        "applied_at",
        "hired_at",
        "scheduled_at",
        "screened_at",
        "reviewed_at",
        "generated_at",
        "completed_at",
        "requested_at",
        "processed_at",
        "expires_at",
        "updated_at",
        check_fields=False,
    )
    def utc_time(self, value: datetime | None):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None

    @field_serializer("score", "reviewer_score", check_fields=False)
    def number(self, value: Decimal | None):
        return float(value) if value is not None else None


class PatchModel(APIModel):
    nonnullable: ClassVar[set[str]] = set()

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Передайте хотя бы одно поле.")
        if any(getattr(self, name) is None for name in self.nonnullable & self.model_fields_set):
            raise ValueError("Обязательное поле не может быть null.")
        return self


class CandidateCreate(APIModel):
    full_name: Title
    email: EmailStr | None = None
    phone: Phone | None = None


class CandidatePatch(PatchModel):
    nonnullable = {"full_name"}
    full_name: Title | None = None
    email: EmailStr | None = None
    phone: Phone | None = None


class CandidateOut(ResourceOut):
    id: UUID
    full_name: str
    email: str | None
    phone: str | None
    source: str | None
    external_id: str | None
    created_at: datetime


class ResumeCreate(APIModel):
    resume_text: Body
    skills: list[Title] = Field(default_factory=list, max_length=100)


class ResumePatch(PatchModel):
    nonnullable = {"resume_text", "skills"}
    resume_text: Body | None = None
    skills: list[Title] | None = Field(default=None, max_length=100)


class ResumeOut(ResourceOut):
    id: UUID
    candidate_id: UUID
    resume_text: str | None
    skills: list[str] | dict | None
    source: str | None
    external_id: str | None
    created_at: datetime


class ApplicationCreate(APIModel):
    vacancy_id: UUID
    candidate_id: UUID
    resume_id: UUID


class ApplicationPatch(APIModel):
    status: ApplicationStatus


class ApplicationOut(ResourceOut):
    id: UUID
    vacancy_id: UUID
    candidate_id: UUID
    resume_id: UUID
    status: str
    applied_at: datetime
    hired_at: datetime | None


class ScreeningResult(APIModel):
    score: Score
    explanation: ShortText


class ScreeningOut(ResourceOut):
    id: UUID
    application_id: UUID
    score: Decimal
    explanation: str
    model_name: str
    screened_at: datetime


class ScreeningAuditCreate(APIModel):
    bias_detected: StrictBool
    comments: ShortText | None = None
    reviewer_score: Score | None = None


class ScreeningAuditOut(ResourceOut):
    id: UUID
    screening_id: UUID
    reviewer_id: UUID
    bias_detected: bool
    comments: str | None
    reviewer_score: Decimal | None
    reviewed_at: datetime


class InterviewCreate(APIModel):
    interviewer_id: UUID
    scheduled_at: AwareDatetime
    duration_minutes: int = Field(default=60, ge=1, le=480)
    notes: ShortText | None = None


class InterviewPatch(PatchModel):
    nonnullable = {"interviewer_id", "scheduled_at", "duration_minutes", "status"}
    interviewer_id: UUID | None = None
    scheduled_at: AwareDatetime | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=480)
    status: InterviewStatus | None = None
    result: InterviewResult | None = None
    notes: ShortText | None = None


class InterviewOut(ResourceOut):
    id: UUID
    application_id: UUID
    interviewer_id: UUID
    scheduled_at: datetime
    duration_minutes: int
    status: str
    result: str | None
    notes: str | None


class TaskCreate(APIModel):
    stage_day: Literal[30, 60, 90]
    title: Title
    description: ShortText | None = None
    due_date: date | None = None


class GeneratedTask(APIModel):
    stage_day: Literal[30, 60, 90]
    title: Title
    description: ShortText


class TaskCollection(APIModel):
    tasks: list[GeneratedTask] = Field(min_length=3, max_length=60)

    @model_validator(mode="after")
    def all_stages(self) -> Self:
        if {task.stage_day for task in self.tasks} != {30, 60, 90}:
            raise ValueError("Нужны задачи для этапов 30, 60 и 90 дней.")
        return self


class PlanCreate(APIModel):
    start_date: StartDate
    tasks: list[TaskCreate] = Field(min_length=3, max_length=60)

    @model_validator(mode="after")
    def all_stages(self) -> Self:
        if {task.stage_day for task in self.tasks} != {30, 60, 90}:
            raise ValueError("Нужны задачи для этапов 30, 60 и 90 дней.")
        return self


class PlanGenerate(APIModel):
    start_date: StartDate


class PlanPatch(APIModel):
    status: Literal["completed", "cancelled"]


class TaskPatch(PatchModel):
    nonnullable = {"title", "is_completed"}
    title: Title | None = None
    description: ShortText | None = None
    due_date: date | None = None
    is_completed: StrictBool | None = None


class TaskOut(ResourceOut):
    id: UUID
    plan_id: UUID
    stage_day: int
    title: str
    description: str | None
    due_date: date | None
    is_completed: bool
    completed_at: datetime | None


class PlanOut(ResourceOut):
    id: UUID
    application_id: UUID
    approved_by: UUID | None
    start_date: date
    status: str
    generated_at: datetime


class PlanDetail(PlanOut):
    tasks: list[TaskOut]


class DeletionCreate(APIModel):
    candidate_id: UUID


class DeletionOut(ResourceOut):
    id: UUID
    candidate_id: UUID | None
    requested_by: UUID | None
    status: str
    requested_at: datetime
    processed_at: datetime | None


class UserOut(ResourceOut):
    id: UUID
    organization_id: UUID
    full_name: str
    email: str
    role: str
    is_active: bool


class UserCreate(APIModel):
    full_name: Title
    email: EmailStr
    password: Password
    role: Role = "hr"


class UserPatch(PatchModel):
    nonnullable = {"full_name", "role", "is_active", "password"}
    full_name: Title | None = None
    role: Role | None = None
    is_active: StrictBool | None = None
    password: Password | None = None


class Register(APIModel):
    organization_name: Title
    full_name: Title
    email: EmailStr
    password: Password


class Login(APIModel):
    organization_id: UUID
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)


class AuthOut(ResourceOut):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut


class AuditOut(ResourceOut):
    id: UUID
    actor_id: UUID | None
    action: str
    entity_type: str
    entity_id: UUID
    created_at: datetime


class IntegrationInput(APIModel):
    access_token: SecretStr | None = Field(default=None, min_length=1, max_length=10000)
    app_key: SecretStr | None = Field(default=None, min_length=1, max_length=10000)
    employer_id: ExternalID | None = None


class IntegrationOut(ResourceOut):
    provider: Provider
    connected: bool
    employer_id: str | None = None
    updated_at: datetime | None = None


class SyncInput(APIModel):
    page: int = Field(default=0, ge=0, le=1000)
    limit: int = Field(default=20, ge=1, le=50)
