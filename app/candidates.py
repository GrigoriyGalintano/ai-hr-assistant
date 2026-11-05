import io
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, Query, Response, UploadFile
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from sqlalchemy import delete, or_, select

from app.common import Page, PageParams, audit_event, conflict, owned, owned_query, paginate
from app.dependencies import SessionDep, UserDep, require_editor
from app.errors import APIError
from app.hr_schemas import (
    AuditOut,
    CandidateCreate,
    CandidateOut,
    CandidatePatch,
    DeletionCreate,
    DeletionOut,
    ResumeCreate,
    ResumeOut,
    ResumePatch,
)
from app.models import Application, AuditLog, Candidate, DeletionRequest, Resume, utcnow

router = APIRouter(prefix="/api/v1", tags=["candidates"])


@router.post("/candidates", response_model=CandidateOut, status_code=201)
def create_candidate(body: CandidateCreate, session: SessionDep, user: UserDep):
    require_editor(user)
    candidate = Candidate(
        **body.model_dump(), organization_id=user.organization_id, source="manual"
    )
    session.add(candidate)
    audit_event(session, user, candidate, "candidate.created")
    session.commit()
    return candidate


@router.get("/candidates", response_model=Page[CandidateOut])
def candidates(
    session: SessionDep,
    user: UserDep,
    page: Annotated[PageParams, Depends()],
    search: Annotated[str | None, Query(max_length=255)] = None,
):
    query = owned_query(Candidate, user)
    if search and search.strip():
        query = query.where(
            or_(
                Candidate.full_name.icontains(search.strip(), autoescape=True),
                Candidate.email.icontains(search.strip(), autoescape=True),
            )
        )
    return paginate(
        session, query.order_by(Candidate.created_at.desc(), Candidate.id), CandidateOut, page
    )


@router.get("/candidates/{candidate_id}", response_model=CandidateOut)
def candidate(candidate_id: UUID, session: SessionDep, user: UserDep):
    return owned(session, user, Candidate, candidate_id)


@router.patch("/candidates/{candidate_id}", response_model=CandidateOut)
def patch_candidate(candidate_id: UUID, body: CandidatePatch, session: SessionDep, user: UserDep):
    require_editor(user)
    row = owned(session, user, Candidate, candidate_id, lock=True)
    for name, value in body.model_dump(exclude_unset=True).items():
        setattr(row, name, value)
    audit_event(session, user, row, "candidate.updated")
    session.commit()
    return row


def erase_candidate(session, user, candidate):
    requests = session.scalars(
        select(DeletionRequest)
        .where(
            DeletionRequest.candidate_id == candidate.id,
            DeletionRequest.organization_id == user.organization_id,
        )
        .with_for_update()
    ).all()
    now = utcnow()
    for record in requests:
        record.status = "completed"
        record.processed_at = now
    audit_event(session, user, candidate, "candidate.deleted")
    session.execute(delete(Candidate).where(Candidate.id == candidate.id))
    # All dependent resumes, applications, screenings, interviews and plans are
    # removed by the original schema's foreign keys. Requests remain anonymous.
    session.flush()
    for record in requests:
        session.refresh(record)


@router.delete("/candidates/{candidate_id}", status_code=204)
def delete_candidate(candidate_id: UUID, session: SessionDep, user: UserDep):
    require_editor(user)
    row = owned(session, user, Candidate, candidate_id, lock=True)
    session.add(
        DeletionRequest(
            organization_id=user.organization_id,
            candidate_id=row.id,
            requested_by=user.id,
        )
    )
    session.flush()
    erase_candidate(session, user, row)
    session.commit()
    return Response(status_code=204)


def save_resume(session, user, candidate_id, body):
    owned(session, user, Candidate, candidate_id, lock=True)
    resume = Resume(**body.model_dump(), candidate_id=candidate_id, source="manual")
    session.add(resume)
    audit_event(session, user, resume, "resume.created")
    session.commit()
    return resume


@router.post(
    "/candidates/{candidate_id}/resumes",
    response_model=ResumeOut,
    status_code=201,
    tags=["resumes"],
)
def create_resume(candidate_id: UUID, body: ResumeCreate, session: SessionDep, user: UserDep):
    require_editor(user)
    return save_resume(session, user, candidate_id, body)


def extract_resume(filename: str, data: bytes) -> str:
    if not data or len(data) > 2 * 1024 * 1024:
        raise APIError(400, "FILE_SIZE", "Размер резюме должен быть от 1 байта до 2 МБ.")
    extension = Path(filename).suffix.lower()
    try:
        if extension == ".txt":
            content = data.decode("utf-8-sig")
        elif extension == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise ValueError("Invalid PDF header")
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 20:
                raise ValueError("Encrypted PDF or too many pages")
            parts = []
            for page in reader.pages:
                parts.append(page.extract_text() or "")
                if sum(map(len, parts)) > 50000:
                    raise ValueError("Too much text")
            content = "\n".join(parts)
        elif extension == ".docx":
            with ZipFile(io.BytesIO(data)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 10 * 1024 * 1024:
                    raise ValueError("Archive too large")
                document = archive.getinfo("word/document.xml")
                if document.file_size > 5 * 1024 * 1024:
                    raise ValueError("Document too large")
                root = ElementTree.fromstring(archive.read(document))
                namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                content = "\n".join(
                    "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
                    for paragraph in root.iter(namespace + "p")
                )
        else:
            raise APIError(415, "FILE_TYPE", "Поддерживаются TXT, PDF и DOCX.")
    except APIError:
        raise
    except (
        ValueError,
        KeyError,
        BadZipFile,
        ElementTree.ParseError,
        OSError,
        PyPdfError,
        RuntimeError,
    ):
        raise APIError(400, "INVALID_FILE", "Не удалось извлечь текст резюме.") from None
    if not content.strip() or len(content) > 50000 or "\x00" in content:
        raise APIError(400, "INVALID_FILE", "Нужен текст резюме длиной до 50000 символов.")
    return content.strip()


@router.post(
    "/candidates/{candidate_id}/resumes/upload",
    response_model=ResumeOut,
    status_code=201,
    tags=["resumes"],
)
def upload_resume(candidate_id: UUID, file: UploadFile, session: SessionDep, user: UserDep):
    require_editor(user)
    owned(session, user, Candidate, candidate_id)
    content = extract_resume(file.filename or "", file.file.read(2 * 1024 * 1024 + 1))
    return save_resume(session, user, candidate_id, ResumeCreate(resume_text=content))


@router.get("/candidates/{candidate_id}/resumes", response_model=Page[ResumeOut], tags=["resumes"])
def resumes(
    candidate_id: UUID, session: SessionDep, user: UserDep, page: Annotated[PageParams, Depends()]
):
    owned(session, user, Candidate, candidate_id)
    query = (
        select(Resume)
        .where(Resume.candidate_id == candidate_id)
        .order_by(Resume.created_at.desc(), Resume.id)
    )
    return paginate(session, query, ResumeOut, page)


@router.get("/resumes/{resume_id}", response_model=ResumeOut, tags=["resumes"])
def resume(resume_id: UUID, session: SessionDep, user: UserDep):
    return owned(session, user, Resume, resume_id)


def editable_resume(session, user, resume_id):
    row = owned(session, user, Resume, resume_id, lock=True)
    if session.scalar(select(Application.id).where(Application.resume_id == row.id).limit(1)):
        conflict("Резюме уже используется в отклике. Создайте новую версию резюме.")
    return row


@router.patch("/resumes/{resume_id}", response_model=ResumeOut, tags=["resumes"])
def patch_resume(resume_id: UUID, body: ResumePatch, session: SessionDep, user: UserDep):
    require_editor(user)
    row = editable_resume(session, user, resume_id)
    for name, value in body.model_dump(exclude_unset=True).items():
        setattr(row, name, value)
    audit_event(session, user, row, "resume.updated")
    session.commit()
    return row


@router.delete("/resumes/{resume_id}", status_code=204, tags=["resumes"])
def delete_resume(resume_id: UUID, session: SessionDep, user: UserDep):
    require_editor(user)
    row = editable_resume(session, user, resume_id)
    audit_event(session, user, row, "resume.deleted")
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.post("/deletion-requests", response_model=DeletionOut, status_code=201, tags=["privacy"])
def request_deletion(body: DeletionCreate, session: SessionDep, user: UserDep):
    require_editor(user)
    owned(session, user, Candidate, body.candidate_id, lock=True)
    existing = session.scalar(
        owned_query(DeletionRequest, user).where(
            DeletionRequest.candidate_id == body.candidate_id,
            DeletionRequest.status == "pending",
        )
    )
    if existing:
        conflict("Запрос на удаление уже создан.")
    row = DeletionRequest(
        organization_id=user.organization_id,
        candidate_id=body.candidate_id,
        requested_by=user.id,
    )
    session.add(row)
    audit_event(session, user, row, "deletion.requested")
    session.commit()
    return row


@router.get("/deletion-requests", response_model=Page[DeletionOut], tags=["privacy"])
def deletion_requests(
    session: SessionDep,
    user: UserDep,
    page: Annotated[PageParams, Depends()],
    status: Literal["pending", "completed"] | None = None,
):
    require_editor(user)
    query = owned_query(DeletionRequest, user)
    if status:
        query = query.where(DeletionRequest.status == status)
    return paginate(
        session,
        query.order_by(DeletionRequest.requested_at.desc(), DeletionRequest.id),
        DeletionOut,
        page,
    )


@router.get("/deletion-requests/{request_id}", response_model=DeletionOut, tags=["privacy"])
def deletion_request(request_id: UUID, session: SessionDep, user: UserDep):
    require_editor(user)
    return owned(session, user, DeletionRequest, request_id)


@router.post(
    "/deletion-requests/{request_id}/process", response_model=DeletionOut, tags=["privacy"]
)
def process_deletion(request_id: UUID, session: SessionDep, user: UserDep):
    require_editor(user)
    # Lock the candidate first, matching direct deletion's lock order.
    row = owned(session, user, DeletionRequest, request_id)
    if row.candidate_id:
        candidate = owned(session, user, Candidate, row.candidate_id, lock=True)
        erase_candidate(session, user, candidate)
    else:
        row = owned(session, user, DeletionRequest, request_id, lock=True)
        if row.status != "completed":
            row.status = "completed"
            row.processed_at = utcnow()
    audit_event(session, user, row, "deletion.completed")
    session.commit()
    session.refresh(row)
    return row


@router.get("/audit-logs", response_model=Page[AuditOut], tags=["audit"])
def audit_logs(
    session: SessionDep,
    user: UserDep,
    page: Annotated[PageParams, Depends()],
    entity_id: Annotated[UUID | None, Query(alias="entityId")] = None,
):
    require_editor(user)
    query = select(AuditLog).where(AuditLog.organization_id == user.organization_id)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    return paginate(
        session, query.order_by(AuditLog.created_at.desc(), AuditLog.id), AuditOut, page
    )
