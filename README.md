# AI HR Assistant

Backend для рекрутинга и адаптации сотрудников. Стек: FastAPI, PostgreSQL,
SQLAlchemy. Есть вакансии, кандидаты, резюме, отклики, интервью, AI-скрининг,
планы адаптации, HR-дашборд и импорт из hh.ru/SuperJob.

## Запуск

Скопируйте `.env.example` в `.env` и замените `API_TOKEN` случайной строкой.
Нужны Docker и Docker Compose.

```sh
docker compose up -d --build --wait
docker compose exec api python -m scripts.seed_dev
```

Swagger доступен по адресу http://localhost:8000/docs.
Миграции применяются при старте. Данные БД сохраняются в volume.
Команда `seed_dev` создаёт локальную организацию и HR-пользователя.
Порт PostgreSQL на хосте: 55433, меняется через `POSTGRES_PORT` в `.env`.

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

## Остальные методы

Все адреса ниже имеют префикс `/api/v1`. Полный список полей и примеры есть в Swagger.

| Модуль | Адреса | Возможности |
| --- | --- | --- |
| Авторизация | `/auth/register`, `/auth/login`, `/auth/logout`, `/auth/me` | Регистрация организации, вход, выход, текущий пользователь |
| Пользователи | `/users`, `/users/{id}` | Список, создание, роли, пароль, деактивация |
| Кандидаты | `/candidates`, `/candidates/{id}` | Создание, список, поиск, изменение, удаление |
| Резюме | `/candidates/{id}/resumes`, `/resumes/{id}` | Версии резюме, текст, навыки, изменение и удаление неиспользуемых версий |
| Загрузка | `/candidates/{id}/resumes/upload` | TXT, PDF с текстовым слоем, DOCX; до 2 МБ и 20 страниц PDF |
| Отклики | `/applications`, `/applications/{id}` | Связь вакансии, кандидата и резюме, этапы подбора |
| Скрининг | `/applications/{id}/screenings`, `/screenings/{id}` | Оценка 0-100, объяснение, история оценок |
| Проверка AI | `/screenings/{id}/audits` | Проверка HR, признак предвзятости, оценка рекрутера |
| Интервью | `/applications/{id}/interviews`, `/interviews/{id}` | Назначение, перенос, результат, отмена; общий список `/interviews` |
| Адаптация | `/applications/{id}/onboarding-plan` | Создание и получение плана после найма |
| Генерация | `/applications/{id}/onboarding-plan/generate` | Генерация черновика через GigaChat |
| Планы | `/onboarding-plans`, `/onboarding-plans/{id}` | Список, карточка, утверждение `/approve`, прогресс `/progress` |
| Задачи | `/onboarding-plans/{id}/tasks`, `/onboarding-tasks/{id}` | Задачи этапов 30/60/90, сроки и выполнение |
| Аналитика | `/dashboard` | Воронка, наймы, средний срок найма, интервью, адаптация, проверки AI |
| Удаление данных | `/deletion-requests`, `/deletion-requests/{id}/process` | Запрос и выполнение удаления данных кандидата |
| Аудит | `/audit-logs` | Журнал действий своей организации |

Списки поддерживают `page` и `limit`. Резюме, связанное с откликом, не изменяется:
для обновления создаётся новая версия. Интервью одного сотрудника не могут пересекаться.
После AI-скрининга перед оффером и наймом нужна проверка последней оценки HR.
AI не принимает решение о найме. План адаптации сначала утверждается HR, затем
можно отмечать задачи. Завершить план можно после выполнения всех задач.

## Доступ

Вход выполняется через `POST /auth/login` с `organizationId`, `email`, `password`.
Ответ содержит `accessToken`, который передаётся как Bearer-токен. Сессия действует
`SESSION_HOURS` часов и отзывается при выходе или смене пароля.
Пароли хранятся как scrypt-хеши, в БД сохраняются только хеши токенов сессий.

Регистрация организаций по умолчанию отключена. Для неё задайте
`ALLOW_REGISTRATION=true`. Первого администратора существующей организации можно создать:

```sh
docker compose exec api python -m scripts.create_admin --organization-id 00000000-0000-0000-0000-000000000001 --email admin@example.com --name Admin
```

Пароль запрашивается интерактивно. Пользователей создаёт администратор.
Роли `hr` и `admin` разрешают изменения, `viewer` разрешает просмотр.
Доступ ограничен своей организацией. Изменения записываются в аудит.
Статический `API_TOKEN`, привязанный к `API_USER_ID`, сохранён для локальной разработки;
его можно убрать из `.env`. Для внешнего доступа нужен HTTPS через reverse proxy.

Исходная ER-схема находится в `sql/001_initial.sql`. Миграция `002_vacancy_api.sql`
добавляет поля вакансий. `003_hr_workflows.sql` добавляет сессии, подключения
интеграций, длительность интервью и оценку рекрутера. Существующие записи сохраняются,
повторный запуск мигратора безопасен.

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
Скрининг и генерация плана проверяют структуру ответа модели перед сохранением.
Общий лимит AI-операции задаётся `AI_WORKFLOW_TIMEOUT`, максимум 30 секунд.
Без ключа GigaChat методы возвращают 503; ручное ведение подбора и адаптации доступно.

## hh.ru и SuperJob

Задайте `INTEGRATION_ENCRYPTION_KEY`, один постоянный ключ Fernet. Команда генерации
есть в `.env.example`. Ключ нужен для шифрования токенов в БД; при его замене
подключения придётся настроить заново.

Администратор подключает сервис через `PUT /integrations/{provider}`:
`provider` принимает `hh` или `superjob`, тело содержит `accessToken`, для SuperJob
также `appKey`. Необязательный `employerId` проверяется по аккаунту провайдера.
Токен проверяется внешним сервисом перед сохранением и не возвращается в ответах API.

- `GET /integrations` показывает подключённые сервисы.
- `POST /integrations/{provider}/vacancies/{externalId}/import` импортирует вакансию.
- `POST /integrations/{provider}/vacancies/sync` загружает одну страницу вакансий работодателя.
- `POST /integrations/{provider}/resumes/{externalId}/import` импортирует доступное резюме.
- `DELETE /integrations/{provider}` удаляет подключение.

Для синхронизации передаются `page` (с нуля) и `limit` (1-50). Повторный импорт
обновляет существующую вакансию, сохраняя её локальный статус. Изменившееся резюме
создаёт новую версию. При истечении внешнего токена подключение нужно обновить через PUT.
Доступ к резюме зависит от прав и тарифа внешнего сервиса.
