import asyncio
import math
import ssl
import time
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings


class GigaChatError(Exception):
    def __init__(self, code: str, message: str, status: int = 502):
        self.code = code
        self.status = status
        super().__init__(message)


class TokenResponse(BaseModel):
    access_token: str = Field(min_length=1)
    expires_at: float = Field(gt=0, allow_inf_nan=False)


class Message(BaseModel):
    content: str = Field(min_length=1)


class Choice(BaseModel):
    message: Message


class ChatResponse(BaseModel):
    choices: list[Choice] = Field(min_length=1)


class GigaChatClient:
    """Text completion client with a cached OAuth token and one refresh after HTTP 401."""

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        context = ssl.create_default_context()
        if settings.gigachat_ca_bundle:
            context.load_verify_locations(str(settings.gigachat_ca_bundle))
        self.http = httpx.AsyncClient(
            verify=context,
            timeout=httpx.Timeout(settings.gigachat_timeout),
            transport=transport,
            follow_redirects=False,
        )
        self._token: str | None = None
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self):
        await self.http.aclose()

    async def _post(self, url: str, **kwargs) -> httpx.Response:
        try:
            return await self.http.post(url, **kwargs)
        except httpx.TimeoutException:
            raise GigaChatError("GIGACHAT_TIMEOUT", "GigaChat не ответил вовремя.", 504) from None
        except httpx.RequestError:
            raise GigaChatError(
                "GIGACHAT_UNAVAILABLE", "Не удалось подключиться к GigaChat."
            ) from None

    @staticmethod
    def _check_status(response: httpx.Response):
        if 200 <= response.status_code < 300:
            return
        if response.status_code == 429:
            raise GigaChatError("GIGACHAT_RATE_LIMIT", "Превышен лимит запросов GigaChat.", 503)
        if response.status_code in {401, 403}:
            raise GigaChatError("GIGACHAT_AUTH_ERROR", "GigaChat отклонил авторизацию.")
        raise GigaChatError("GIGACHAT_UPSTREAM_ERROR", "GigaChat вернул ошибку.")

    async def _access_token(self, *, rejected: str | None = None) -> str:
        async with self._token_lock:
            # Reuse a token another coroutine has refreshed in the meantime.
            if self._token and self._token != rejected and time.time() < self._expires_at - 30:
                return self._token
            key = self.settings.gigachat_auth_key
            if key is None or not key.get_secret_value().strip():
                raise GigaChatError("GIGACHAT_NOT_CONFIGURED", "Не задан ключ GigaChat.", 503)
            response = await self._post(
                str(self.settings.gigachat_auth_url),
                headers={
                    "Authorization": f"Basic {key.get_secret_value()}",
                    "RqUID": str(uuid4()),
                    "Accept": "application/json",
                },
                data={"scope": self.settings.gigachat_scope},
            )
            self._check_status(response)
            try:
                data = TokenResponse.model_validate(response.json())
                # GigaChat installations return Unix time in milliseconds or seconds.
                expiry = (
                    data.expires_at / 1000 if data.expires_at > 100_000_000_000 else data.expires_at
                )
                if not math.isfinite(expiry) or expiry <= time.time() + 30:
                    raise ValueError("Expired token")
            except (ValueError, ValidationError):
                raise GigaChatError(
                    "GIGACHAT_INVALID_RESPONSE", "Некорректный токен GigaChat."
                ) from None
            self._token = data.access_token
            self._expires_at = expiry
            return self._token

    async def ask(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Запрос не может быть пустым.")
        if len(prompt) > 50000:
            raise ValueError("Запрос не должен превышать 50000 символов.")
        token = await self._access_token()
        for attempt in range(2):
            response = await self._post(
                f"{str(self.settings.gigachat_base_url).rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                json={
                    "model": self.settings.gigachat_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
            )
            if response.status_code == 401 and attempt == 0:
                token = await self._access_token(rejected=token)
                continue
            self._check_status(response)
            try:
                result = ChatResponse.model_validate(response.json()).choices[0].message.content
                if not result.strip():
                    raise ValueError("Empty response")
            except (ValueError, ValidationError):
                raise GigaChatError(
                    "GIGACHAT_INVALID_RESPONSE", "GigaChat вернул некорректный ответ."
                ) from None
            return result
        raise AssertionError("Unreachable")
