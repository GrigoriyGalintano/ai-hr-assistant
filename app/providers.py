import asyncio
import html
from html.parser import HTMLParser

import httpx
from pydantic import ValidationError

from app.errors import APIError
from app.hr_schemas import CandidateCreate, ResumeCreate
from app.schemas import VacancyCreate


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        if tag in {"p", "br", "li", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        if tag in {"p", "li", "div"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain(value):
    if value is None:
        return ""
    parser = PlainText()
    parser.feed(str(value))
    return html.unescape("".join(parser.parts)).strip()


def invalid_provider_data():
    return APIError(
        502, "PROVIDER_INVALID_RESPONSE", "Внешний сервис вернул данные неверного формата."
    )


class ProviderClient:
    def __init__(self, settings, *, transport=None):
        self.http = httpx.AsyncClient(
            timeout=settings.external_api_timeout,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": settings.external_user_agent},
        )

    async def aclose(self):
        await self.http.aclose()

    async def get(self, provider, path, credentials, *, params=None):
        base = {"hh": "https://api.hh.ru", "superjob": "https://api.superjob.ru/2.0"}[provider]
        headers = {"Accept": "application/json"}
        if credentials.get("access_token"):
            headers["Authorization"] = "Bearer " + credentials["access_token"]
        if provider == "superjob":
            headers["X-Api-App-Id"] = credentials["app_key"]
        try:
            response = await self.http.get(base + path, headers=headers, params=params)
        except httpx.TimeoutException:
            raise APIError(504, "PROVIDER_TIMEOUT", "Внешний сервис не ответил вовремя.") from None
        except httpx.RequestError:
            raise APIError(502, "PROVIDER_UNAVAILABLE", "Внешний сервис недоступен.") from None
        if response.status_code in {401, 403}:
            raise APIError(
                502, "PROVIDER_AUTH_ERROR", "Проверьте токен и права доступа внешнего сервиса."
            )
        if response.status_code == 404:
            raise APIError(404, "PROVIDER_NOT_FOUND", "Запись во внешнем сервисе не найдена.")
        if response.status_code == 429:
            raise APIError(503, "PROVIDER_RATE_LIMIT", "Внешний сервис ограничил частоту запросов.")
        if response.status_code != 200:
            raise APIError(502, "PROVIDER_ERROR", "Внешний сервис вернул ошибку.")
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Expected an object")
            return result
        except ValueError:
            raise invalid_provider_data() from None

    async def vacancy(self, provider, external_id, credentials):
        suffix = "/" if provider == "superjob" else ""
        return await self.get(provider, f"/vacancies/{external_id}{suffix}", credentials)

    async def resume(self, provider, external_id, credentials):
        suffix = "/" if provider == "superjob" else ""
        return await self.get(provider, f"/resumes/{external_id}{suffix}", credentials)

    async def vacancies(self, provider, credentials, employer_id, page, limit):
        if provider == "hh":
            if not employer_id:
                raise APIError(
                    400, "EMPLOYER_REQUIRED", "Укажите employerId для синхронизации hh.ru."
                )
            path = f"/employers/{employer_id}/vacancies/active"
            params = {"page": page, "per_page": limit}
            key = "items"
        else:
            path = "/hr/vacancies/"
            params = {"page": page, "count": limit, "status": "active"}
            key = "objects"
        listing = await self.get(provider, path, credentials, params=params)
        rows = listing.get(key)
        if not isinstance(rows, list) or len(rows) > limit:
            raise invalid_provider_data()
        ids = []
        for row in rows:
            if (
                not isinstance(row, dict)
                or not str(row.get("id", "")).isdigit()
                or len(str(row["id"])) > 100
            ):
                raise invalid_provider_data()
            ids.append(str(row["id"]))
        if len(set(ids)) != len(ids):
            raise invalid_provider_data()
        semaphore = asyncio.Semaphore(5)

        async def fetch(external_id):
            async with semaphore:
                return await self.vacancy(provider, external_id, credentials)

        try:
            async with asyncio.timeout(45):
                details = await asyncio.gather(*(fetch(external_id) for external_id in ids))
        except TimeoutError:
            raise APIError(
                504, "PROVIDER_TIMEOUT", "Не удалось завершить страницу синхронизации вовремя."
            ) from None
        if provider == "hh":
            pages = listing.get("pages")
            if isinstance(pages, bool) or not isinstance(pages, int) or pages < 0:
                raise invalid_provider_data()
            has_more = pages > page + 1
        else:
            has_more = listing.get("more")
            if not isinstance(has_more, bool):
                raise invalid_provider_data()
        return details, has_more


def normalize_vacancy(provider, data):
    try:
        external_id = str(data["id"])
        if not external_id.isdigit() or len(external_id) > 100:
            raise ValueError("Invalid ID")
        if provider == "hh":
            description = plain(data.get("description"))
            snippet = data.get("snippet") or {}
            requirements = plain(snippet.get("requirement")) or description
            salary = data.get("salary") or data.get("salary_range") or {}
            values = {
                "title": data["name"],
                "description": description,
                "requirements": requirements,
                "responsibilities": plain(snippet.get("responsibility")) or None,
                "location": (data.get("area") or {}).get("name"),
                "employment_type": (data.get("employment") or {}).get("id"),
                "experience": (data.get("experience") or {}).get("name"),
                "salary_from": salary.get("from"),
                "salary_to": salary.get("to"),
                "currency": salary.get("currency"),
            }
            status = "closed" if data.get("archived") else "active"
        else:
            requirements = plain(data.get("candidat"))
            description = plain(data.get("vacancyRichText")) or "\n".join(
                filter(
                    None,
                    [
                        plain(data.get("work")),
                        requirements,
                        plain(data.get("compensation")),
                    ],
                )
            )
            values = {
                "title": data["profession"],
                "description": description,
                "requirements": requirements or description,
                "responsibilities": plain(data.get("work")) or None,
                "location": (data.get("town") or {}).get("title"),
                "employment_type": str((data.get("type_of_work") or {}).get("id", "")) or None,
                "experience": (data.get("experience") or {}).get("title"),
                "salary_from": data.get("payment_from") or None,
                "salary_to": data.get("payment_to") or None,
                "currency": data.get("currency"),
            }
            status = "active" if data.get("published", True) else "closed"
        if values["currency"]:
            currency = str(values["currency"]).upper()
            values["currency"] = {"RUR": "RUB", "RUB": "RUB", "RUBL": "RUB"}.get(currency, currency)
        body = VacancyCreate.model_validate(values)
        return external_id, body, status
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError):
        raise invalid_provider_data() from None


def normalize_resume(provider, data):
    try:
        external_id = str(data["id"])
        if not external_id or len(external_id) > 100:
            raise ValueError("Invalid ID")
        if provider == "hh":
            name = " ".join(
                filter(
                    None, [data.get("last_name"), data.get("first_name"), data.get("middle_name")]
                )
            )
            skills = [item["name"] for item in data.get("skill_set", [])]
            experience = data.get("experience", [])
            parts = [plain(data.get("title")), plain(data.get("skills"))]
            for row in experience:
                parts.append(
                    "\n".join(
                        filter(
                            None,
                            [
                                plain(row.get("company")),
                                plain(row.get("position")),
                                str(row.get("start") or ""),
                                str(row.get("end") or ""),
                                plain(row.get("description")),
                            ],
                        )
                    )
                )
            for row in (data.get("education") or {}).get("primary", []):
                parts.append(
                    " ".join(
                        str(row.get(key) or "")
                        for key in ("name", "organization", "result", "year")
                    )
                )
            email = phone = None
            for contact in data.get("contact", []):
                kind = (contact.get("type") or {}).get("id")
                value = contact.get("value")
                if kind == "email" and isinstance(value, str):
                    email = value
                if kind in {"cell", "work", "home"}:
                    phone = value.get("formatted") if isinstance(value, dict) else value
        else:
            name = " ".join(
                filter(None, [data.get("lastname"), data.get("firstname"), data.get("middlename")])
            )
            skills = []
            parts = [
                plain(data.get("profession")),
                plain(data.get("achievements")),
                plain(data.get("additional_info")),
            ]
            for row in data.get("work_history", []):
                parts.append(
                    "\n".join(
                        plain(row.get(key))
                        for key in ("name", "position", "work", "date_start", "date_end")
                    )
                )
            email = data.get("email")
            phones = data.get("phones") or []
            phone = phones[0].get("number") if phones and isinstance(phones[0], dict) else None
        content = "\n".join(part for part in parts if part.strip())
        candidate = CandidateCreate(
            full_name=name or f"Кандидат {external_id}", email=email, phone=phone
        )
        resume = ResumeCreate(resume_text=content, skills=skills)
        return external_id, candidate, resume
    except (KeyError, TypeError, ValueError, AttributeError, ValidationError):
        raise invalid_provider_data() from None
