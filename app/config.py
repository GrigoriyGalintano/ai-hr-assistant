from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://hr:hr@127.0.0.1:55433/hr_assistant"
    api_token: SecretStr | None = None
    api_user_id: UUID | None = None

    gigachat_auth_key: SecretStr | None = None
    gigachat_scope: Literal["GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"] = (
        "GIGACHAT_API_PERS"
    )
    gigachat_model: str = "GigaChat-2"
    gigachat_base_url: HttpUrl = HttpUrl("https://api.giga.chat/v1")
    gigachat_auth_url: HttpUrl = HttpUrl("https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
    gigachat_timeout: float = Field(default=30, gt=0, le=120)
    gigachat_ca_bundle: Path | None = None

    @field_validator("gigachat_ca_bundle", mode="before")
    @classmethod
    def empty_ca_path(cls, value):
        return None if value == "" else value
