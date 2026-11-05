# AI HR Assistant

Backend для рекрутинга и адаптации сотрудников. Стек: FastAPI, PostgreSQL,
SQLAlchemy. Реализованы API вакансий и клиент GigaChat для текстовых запросов.

## Запуск

Скопируйте `.env.example` в `.env` и замените `API_TOKEN` случайной строкой.
Нужны Docker и Docker Compose.

```sh
docker compose up -d --build
docker compose exec api python -m scripts.seed_dev
```

Swagger доступен по адресу http://localhost:8000/docs.
Миграции применяются при старте. Данные БД сохраняются в volume.
Команда `seed_dev` создаёт локальную организацию и HR-пользователя.

Без Docker для приложения, Python 3.12+:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
docker compose up -d db
python -m scripts.migrate
python -m scripts.seed_dev
uvicorn app.main:create_app --factory --reload
```

## API вакансий

Заголовок авторизации: `Authorization: Bearer <API_TOKEN>`.

| Метод | Адрес | Назначение |
| --- | --- | --- |
| POST | `/api/v1/vacancies` | Создание, ответ 201 |
| GET | `/api/v1/vacancies` | Список с пагинацией |
| GET | `/api/v1/vacancies/{id}` | Карточка |
| PATCH | `/api/v1/vacancies/{id}` | Изменение переданных полей |

Обязательные поля: `title`, `description`, `requirements`.
Новая вакансия получает статус `draft` и источник `manual`.
Список поддерживает `page`, `limit` (1-100), `status`, `search`, `sort`, `order`.
Сортировка: `createdAt`, `updatedAt`, `title`. Поиск по названию.
Переходы статуса: `draft -> active`, `active <-> paused`, `active/paused -> closed`.

```sh
curl -X POST http://localhost:8000/api/v1/vacancies \
  -H "Authorization: Bearer <API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"title":"Аналитик","description":"Развитие внутренних систем","requirements":"REST API, SQL"}'
```

Токен привязан к существующему `API_USER_ID`. Организация и роль берутся из БД.
Роли `hr` и `admin` разрешают изменения, `viewer` разрешает просмотр.
Доступ ограничен своей организацией. Создание и изменения записываются в аудит.
Это временная авторизация MVP, без регистрации и входа пользователей.
Для внешнего доступа нужен HTTPS через reverse proxy.

Исходная ER-схема находится в `sql/001_initial.sql`. Миграция `002_vacancy_api.sql`
добавляет поля API и сохраняет существующие записи. Повторный запуск безопасен.

## GigaChat

Укажите `GIGACHAT_AUTH_KEY` в `.env`: Authorization key из кабинета разработчика,
без префикса `Basic`. Модель, scope и адреса также задаются в `.env`.

```sh
python -m scripts.ask_gigachat "Ответь одним словом: привет"
```

HTTP: `POST /api/v1/ai/chat`, тело `{"prompt":"Привет"}`, ответ `{"text":"...","model":"..."}`.
Клиент кэширует OAuth-токен, обновляет его при истечении срока и после 401.
Обрабатывает таймауты, лимиты и ошибки сервиса. TLS проверяется всегда.
Для собственной цепочки сертификатов есть `GIGACHAT_CA_BUNDLE` с путём к PEM-файлу.
Для контейнера файл нужно отдельно примонтировать.
Скрининг резюме в эту версию не входит.
