from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
    model_validator,
)
from pydantic.alias_generators import to_camel

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50000)]
Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
Status = Literal["draft", "active", "paused", "closed"]


class APIModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, from_attributes=True, extra="forbid"
    )


class VacancyFields(APIModel):
    title: Title
    description: Body
    requirements: Body
    responsibilities: Body | None = None
    department: Title | None = None
    location: Title | None = None
    employment_type: Annotated[str, StringConstraints(min_length=1, max_length=50)] | None = None
    experience: Title | None = None
    salary_from: Money | None = None
    salary_to: Money | None = None
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None

    @model_validator(mode="after")
    def check_salary_range(self) -> Self:
        if (
            self.salary_from is not None
            and self.salary_to is not None
            and self.salary_from > self.salary_to
        ):
            raise ValueError("salaryFrom не должен превышать salaryTo.")
        return self


class VacancyCreate(VacancyFields):
    pass


class VacancyPatch(APIModel):
    title: Title | None = None
    description: Body | None = None
    requirements: Body | None = None
    responsibilities: Body | None = None
    department: Title | None = None
    location: Title | None = None
    employment_type: Annotated[str, StringConstraints(min_length=1, max_length=50)] | None = None
    experience: Title | None = None
    salary_from: Money | None = None
    salary_to: Money | None = None
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    status: Status | None = None

    @model_validator(mode="after")
    def check_fields(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Передайте хотя бы одно поле.")
        for field in {"title", "description", "requirements", "status"} & self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"Поле {field} не может быть null.")
        return self


class VacancyOut(APIModel):
    # Existing rows from the supplied ER schema can have null requirements/source.
    id: UUID
    title: str
    description: str
    requirements: str | None
    responsibilities: str | None
    department: str | None
    location: str | None
    employment_type: str | None
    experience: str | None
    salary_from: Decimal | None
    salary_to: Decimal | None
    currency: str | None
    status: str
    source: str | None
    external_id: str | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("salary_from", "salary_to")
    def serialize_money(self, value: Decimal | None) -> float | None:
        return float(value) if value is not None else None

    @field_serializer("created_at", "updated_at")
    def serialize_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class Pagination(APIModel):
    page: int
    limit: int
    total: int
    total_pages: int = Field(serialization_alias="totalPages", validation_alias="total_pages")


class VacancyList(APIModel):
    items: list[VacancyOut]
    pagination: Pagination
