import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException

from app.ai import router as ai_router
from app.ai_workflows import router as screenings_router
from app.auth import router as auth_router
from app.candidates import router as candidates_router
from app.config import Settings
from app.dashboard import router as dashboard_router
from app.errors import APIError, error_payload
from app.gigachat import GigaChatClient, GigaChatError
from app.integrations import router as integrations_router
from app.middleware import BodySizeLimit
from app.onboarding import router as onboarding_router
from app.providers import ProviderClient
from app.recruitment import router as recruitment_router
from app.vacancies import router as vacancies_router

logger = logging.getLogger(__name__)


def error_response(status, code, message, details=None):
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(
        status_code=status,
        headers=headers,
        content=error_payload(code, message, details),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        app.state.settings = settings
        app.state.sessions = sessionmaker(engine, expire_on_commit=False)
        try:
            async with GigaChatClient(settings) as client:
                app.state.gigachat = client
                app.state.providers = ProviderClient(settings)
                try:
                    yield
                finally:
                    await app.state.providers.aclose()
        finally:
            engine.dispose()

    app = FastAPI(title="AI HR Assistant", version="0.2.0", lifespan=lifespan)
    app.add_middleware(BodySizeLimit)

    @app.exception_handler(APIError)
    async def api_error(_request, exc):
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"][1:]),
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return error_response(400, "VALIDATION_ERROR", "Проверьте данные запроса.", details)

    @app.exception_handler(GigaChatError)
    async def gigachat_error(_request, exc):
        return error_response(exc.status, exc.code, str(exc))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request, exc):
        # SQL exceptions may contain query parameters. Do not log their text.
        logger.error("Database operation failed: %s", type(exc).__name__)
        return error_response(503, "DATABASE_UNAVAILABLE", "База данных временно недоступна.")

    @app.exception_handler(IntegrityError)
    async def integrity_error(_request, _exc):
        return error_response(
            409, "DATA_CONFLICT", "Данные изменились или связанный объект недоступен."
        )

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc):
        return error_response(exc.status_code, "HTTP_ERROR", str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected_error(_request, exc):
        logger.error("Unhandled error: %s", type(exc).__name__)
        return error_response(500, "INTERNAL_ERROR", "Внутренняя ошибка сервера.")

    @app.get("/health", tags=["health"])
    def health(request: Request):
        with request.app.state.sessions() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}

    app.include_router(vacancies_router)
    app.include_router(ai_router)
    app.include_router(auth_router)
    app.include_router(candidates_router)
    app.include_router(recruitment_router)
    app.include_router(screenings_router)
    app.include_router(onboarding_router)
    app.include_router(dashboard_router)
    app.include_router(integrations_router)
    return app
