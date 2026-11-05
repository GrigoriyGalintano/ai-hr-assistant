from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.common import audit_event, conflict, require_admin
from app.dependencies import SessionDep, UserDep, require_editor
from app.errors import APIError
from app.hr_schemas import (
    ExternalID,
    IntegrationInput,
    IntegrationOut,
    Provider,
    ResumeOut,
    SyncInput,
)
from app.models import Candidate, IntegrationConnection, Organization, Resume, Vacancy, utcnow
from app.providers import invalid_provider_data, normalize_resume, normalize_vacancy
from app.schemas import APIModel, VacancyOut

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


class ResumeImportOut(APIModel):
    candidate_id: UUID
    resume: ResumeOut
    created: bool


class VacancySyncOut(APIModel):
    created: int
    updated: int
    page: int
    has_more: bool
    items: list[VacancyOut]


def cipher(settings):
    value = settings.integration_encryption_key
    try:
        if value is None:
            raise ValueError("Missing encryption key")
        return Fernet(value.get_secret_value().encode())
    except (ValueError, TypeError):
        raise APIError(
            503, "INTEGRATIONS_NOT_CONFIGURED", "Не настроен ключ шифрования интеграций."
        ) from None


def connection(session, user, provider, *, lock=False):
    query = select(IntegrationConnection).where(
        IntegrationConnection.organization_id == user.organization_id,
        IntegrationConnection.provider == provider,
    )
    if lock:
        query = query.with_for_update()
    row = session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise APIError(409, "INTEGRATION_NOT_CONNECTED", "Сначала подключите внешний сервис.")
    return row


def credentials(row, settings):
    key = cipher(settings)
    try:
        return {
            "access_token": key.decrypt(row.access_token_encrypted.encode()).decode()
            if row.access_token_encrypted
            else None,
            "app_key": key.decrypt(row.app_key_encrypted.encode()).decode()
            if row.app_key_encrypted
            else None,
        }
    except (InvalidToken, UnicodeError, ValueError):
        raise APIError(
            503, "INTEGRATION_KEY_ERROR", "Не удалось прочитать ключи интеграции."
        ) from None


def recheck(session, user):
    session.refresh(user)
    require_editor(user)
    if not user.is_active:
        raise APIError(403, "FORBIDDEN", "Пользователь недоступен.")


@router.get("", response_model=list[IntegrationOut])
def integrations(session: SessionDep, user: UserDep):
    require_editor(user)
    rows = {
        row.provider: row
        for row in session.scalars(
            select(IntegrationConnection).where(
                IntegrationConnection.organization_id == user.organization_id,
            )
        )
    }
    return [
        IntegrationOut(
            provider=provider,
            connected=provider in rows,
            employer_id=rows[provider].employer_id if provider in rows else None,
            updated_at=rows[provider].updated_at if provider in rows else None,
        )
        for provider in ("hh", "superjob")
    ]


@router.put("/{provider}", response_model=IntegrationOut)
async def connect(
    provider: Provider, body: IntegrationInput, request: Request, session: SessionDep, user: UserDep
):
    require_admin(user)
    key = cipher(request.app.state.settings)
    data = {
        "access_token": body.access_token.get_secret_value().strip() if body.access_token else None,
        "app_key": body.app_key.get_secret_value().strip() if body.app_key else None,
    }
    if not data["access_token"] or (provider == "superjob" and not data["app_key"]):
        raise APIError(
            400, "CREDENTIALS_REQUIRED", "Нужен accessToken, а для SuperJob также appKey."
        )
    session.rollback()
    path = "/me" if provider == "hh" else "/user/current/"
    info = await request.app.state.providers.get(provider, path, data)
    if provider == "hh":
        employer_data = info.get("employer")
        if employer_data is not None and not isinstance(employer_data, dict):
            raise invalid_provider_data()
        employer = str((employer_data or {}).get("id") or "")
    else:
        employer = str(info.get("id_client") or "")
    if not employer:
        raise APIError(
            400, "EMPLOYER_ACCOUNT_REQUIRED", "Нужен аккаунт работодателя внешнего сервиса."
        )
    if not employer.isdigit() or len(employer) > 100:
        raise invalid_provider_data()
    if body.employer_id and employer and body.employer_id != employer:
        raise APIError(
            400, "EMPLOYER_MISMATCH", "employerId не соответствует аккаунту внешнего сервиса."
        )
    recheck(session, user)
    require_admin(user)
    session.scalar(
        select(Organization).where(Organization.id == user.organization_id).with_for_update()
    )
    row = session.scalar(
        select(IntegrationConnection)
        .where(
            IntegrationConnection.organization_id == user.organization_id,
            IntegrationConnection.provider == provider,
        )
        .with_for_update()
    )
    if row is None:
        row = IntegrationConnection(organization_id=user.organization_id, provider=provider)
        session.add(row)
    row.access_token_encrypted = key.encrypt(data["access_token"].encode()).decode()
    row.app_key_encrypted = (
        key.encrypt(data["app_key"].encode()).decode() if data["app_key"] else None
    )
    row.employer_id = body.employer_id or employer or None
    row.updated_at = utcnow()
    audit_event(session, user, row, "integration.connected")
    session.commit()
    return IntegrationOut(
        provider=provider, connected=True, employer_id=row.employer_id, updated_at=row.updated_at
    )


@router.delete("/{provider}", status_code=204)
def disconnect(provider: Provider, session: SessionDep, user: UserDep):
    require_admin(user)
    row = connection(session, user, provider, lock=True)
    audit_event(session, user, row, "integration.disconnected")
    session.delete(row)
    session.commit()
    return Response(status_code=204)


def begin_import(session, user, provider, settings):
    row = connection(session, user, provider)
    snapshot = (row.id, row.updated_at)
    result = credentials(row, settings)
    employer_id = row.employer_id
    session.rollback()
    return snapshot, result, employer_id


def finish_import(session, user, provider, snapshot):
    recheck(session, user)
    row = connection(session, user, provider, lock=True)
    if (row.id, row.updated_at) != snapshot:
        conflict("Настройки интеграции изменились. Повторите импорт.")


def store_vacancy(session, user, provider, normalized):
    external_id, body, status = normalized
    row = session.scalar(
        select(Vacancy)
        .where(
            Vacancy.organization_id == user.organization_id,
            Vacancy.source == provider,
            Vacancy.external_id == external_id,
        )
        .order_by(Vacancy.created_at, Vacancy.id)
        .limit(1)
        .with_for_update()
    )
    created = row is None
    now = utcnow()
    if row is None:
        row = Vacancy(
            organization_id=user.organization_id,
            created_by=user.id,
            source=provider,
            external_id=external_id,
            status=status,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    for name, value in body.model_dump().items():
        setattr(row, name, value)
    row.updated_at = now
    audit_event(session, user, row, "vacancy.imported")
    return row, created


@router.post("/{provider}/vacancies/{external_id}/import", response_model=VacancyOut)
async def import_vacancy(
    provider: Provider,
    external_id: ExternalID,
    request: Request,
    session: SessionDep,
    user: UserDep,
):
    require_editor(user)
    if not external_id.isdigit():
        raise APIError(400, "INVALID_EXTERNAL_ID", "ID вакансии должен быть числом.")
    snapshot, auth, _ = begin_import(session, user, provider, request.app.state.settings)
    data = await request.app.state.providers.vacancy(provider, external_id, auth)
    normalized = normalize_vacancy(provider, data)
    if normalized[0] != external_id:
        raise APIError(502, "PROVIDER_INVALID_RESPONSE", "Внешний сервис вернул другую вакансию.")
    finish_import(session, user, provider, snapshot)
    row, _ = store_vacancy(session, user, provider, normalized)
    session.commit()
    return row


@router.post("/{provider}/vacancies/sync", response_model=VacancySyncOut)
async def sync_vacancies(
    provider: Provider, body: SyncInput, request: Request, session: SessionDep, user: UserDep
):
    require_editor(user)
    snapshot, auth, employer = begin_import(session, user, provider, request.app.state.settings)
    rows, has_more = await request.app.state.providers.vacancies(
        provider, auth, employer, body.page, body.limit
    )
    normalized = [normalize_vacancy(provider, row) for row in rows]
    finish_import(session, user, provider, snapshot)
    output = []
    created = 0
    for data in normalized:
        row, is_new = store_vacancy(session, user, provider, data)
        created += int(is_new)
        output.append(row)
    session.commit()
    return VacancySyncOut(
        created=created,
        updated=len(output) - created,
        page=body.page,
        has_more=has_more,
        items=[VacancyOut.model_validate(row) for row in output],
    )


@router.post("/{provider}/resumes/{external_id}/import", response_model=ResumeImportOut)
async def import_resume(
    provider: Provider,
    external_id: ExternalID,
    request: Request,
    session: SessionDep,
    user: UserDep,
):
    require_editor(user)
    snapshot, auth, _ = begin_import(session, user, provider, request.app.state.settings)
    data = await request.app.state.providers.resume(provider, external_id, auth)
    remote_id, profile, body = normalize_resume(provider, data)
    if remote_id != external_id:
        raise APIError(502, "PROVIDER_INVALID_RESPONSE", "Внешний сервис вернул другое резюме.")
    finish_import(session, user, provider, snapshot)
    candidate = session.scalar(
        select(Candidate)
        .where(
            Candidate.organization_id == user.organization_id,
            Candidate.source == provider,
            Candidate.external_id == external_id,
        )
        .order_by(Candidate.created_at, Candidate.id)
        .limit(1)
        .with_for_update()
    )
    if candidate is None:
        candidate = Candidate(
            **profile.model_dump(),
            organization_id=user.organization_id,
            source=provider,
            external_id=external_id,
        )
        session.add(candidate)
        audit_event(session, user, candidate, "candidate.imported")
    else:
        for name, value in profile.model_dump().items():
            if value is not None:
                setattr(candidate, name, value)
        audit_event(session, user, candidate, "candidate.updated")
    previous = session.scalar(
        select(Resume)
        .where(
            Resume.candidate_id == candidate.id,
            Resume.source == provider,
            Resume.external_id == external_id,
        )
        .order_by(Resume.created_at.desc(), Resume.id)
        .limit(1)
    )
    created = (
        previous is None
        or previous.resume_text != body.resume_text
        or previous.skills != body.skills
    )
    if created:
        previous = Resume(
            **body.model_dump(), candidate_id=candidate.id, source=provider, external_id=external_id
        )
        session.add(previous)
        audit_event(session, user, previous, "resume.imported")
    session.commit()
    return ResumeImportOut(
        candidate_id=candidate.id, resume=ResumeOut.model_validate(previous), created=created
    )
