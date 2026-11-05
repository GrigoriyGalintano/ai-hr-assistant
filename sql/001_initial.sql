-- AI-ассистент HR: структура базы данных PostgreSQL.
-- Кодировка файла: UTF-8. PostgreSQL 14 и новее. Расширения не нужны.
--
-- ОСНОВА
-- Таблицы, названия и типы полей, обязательность и связи перенесены из
-- ER-диаграммы в документе «Описание_ER_диаграммы_PostgreSQL_HR (1).docx».
-- Паспорт проекта использован только для понимания назначения системы.
-- В файле нет персональных данных, тестовых записей и справочных INSERT.
--
-- КАК ПРИМЕНИТЬ
-- 1. Создайте пустую базу PostgreSQL, например hr_assistant, средствами
--    pgAdmin, DBeaver или командой CREATE DATABASE hr_assistant;
--    (эту команду выполняют отдельно, вне транзакции данного файла).
-- 2. Подключитесь к созданной базе и выполните ВЕСЬ этот файл как SQL-скрипт.
--    Таблицы создаются в схеме public текущей базы.
--    Вариант через psql:
--    psql -X -v ON_ERROR_STOP=1 -h localhost -U postgres -d hr_assistant -f hr_assistant_schema.sql
-- 3. Для передачи структуры загрузите этот файл на GitHub. Получателю
--    достаточно выполнить шаги 1 и 2 в собственной базе PostgreSQL.
--
-- Файл предназначен для первоначального создания схемы в пустой базе.
-- Он не удаляет и не изменяет уже существующие таблицы. Если таблица с таким
-- именем уже есть, выполнение завершится ошибкой, а транзакция не сохранит
-- частично созданную структуру. IF NOT EXISTS намеренно не используется:
-- оно могло бы скрыть отличия между схемами у разных участников.
-- Имена базы, пользователей PostgreSQL и пароли не являются частью модели.
--
-- ПРИНЯТЫЕ ТЕХНИЧЕСКИЕ РЕШЕНИЯ
-- * Символ * на диаграмме передан через NOT NULL.
-- * Для UUID заданы DEFAULT gen_random_uuid(), для времени создания,
--   отклика, скрининга и проверки - DEFAULT CURRENT_TIMESTAMP.
-- * По умолчанию пользователь активен, задача не выполнена. Результат
--   проверки bias_detected должен быть указан явно.
-- * Составной FK applications(resume_id, candidate_id) гарантирует,
--   что выбранное резюме принадлежит указанному кандидату.
-- * Для onboarding_plans.application_id задан UNIQUE, для stage_day -
--   CHECK (stage_day IN (30, 60, 90)).
-- * Статусы, роли, источники, диапазон score, уникальность email и запрет
--   повторных откликов в Word не определены; ограничения для них не выдуманы.
-- * При удалении кандидата удаляются его резюме, отклики и зависимые
--   интервью, AI-оценки, проверки и адаптация. Запросы на удаление остаются,
--   а их candidate_id становится NULL. Используемое в отклике резюме нельзя
--   удалить отдельно от отклика. Удаление вакансии с откликами запрещено.
-- * Удаление организации с зависимыми записями запрещено. Удаление
--   пользователя, указанного в обязательной связи, запрещено; для него
--   можно использовать is_active = false. Необязательные ссылки на
--   удаляемого пользователя обнуляются.
--
-- ГРАНИЦЫ МОДЕЛИ ИЗ WORD
-- Принадлежность связанных записей одной организации проверяет приложение:
-- автор вакансии, кандидат и вакансия в отклике, интервьюер, проверяющий,
-- согласовавший план, участники журнала и запросов на удаление должны
-- относиться к нужной организации. Файл не добавляет отсутствующие на
-- диаграмме organization_id в дочерние таблицы и не вводит RLS/триггеры.
-- Приложение также проверяет факт найма перед созданием плана, управляет
-- переходами статусов и заполняет аудит. audit_logs.entity_id - ссылка
-- на объект разных типов, поэтому обычного FK для неё нет. Схема сама
-- по себе не обеспечивает разграничение доступа и соблюдение 152-ФЗ.

BEGIN;

SET LOCAL search_path = pg_catalog, public;

-- 1. Организации и доступ.

CREATE TABLE public.organizations (
    id          UUID         NOT NULL DEFAULT gen_random_uuid(),
    name        VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT organizations_pkey PRIMARY KEY (id)
);

CREATE TABLE public.users (
    id               UUID         NOT NULL DEFAULT gen_random_uuid(),
    organization_id  UUID         NOT NULL,
    full_name        VARCHAR(255) NOT NULL,
    email            VARCHAR(320) NOT NULL,
    role             VARCHAR(30)  NOT NULL,
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,

    CONSTRAINT users_pkey PRIMARY KEY (id),
    CONSTRAINT users_organization_fk
        FOREIGN KEY (organization_id)
        REFERENCES public.organizations (id) ON DELETE RESTRICT
);

-- 2. Вакансии, кандидаты, резюме и отклики.

CREATE TABLE public.vacancies (
    id               UUID         NOT NULL DEFAULT gen_random_uuid(),
    organization_id  UUID         NOT NULL,
    created_by       UUID         NOT NULL,
    title            VARCHAR(255) NOT NULL,
    requirements     TEXT,
    status           VARCHAR(30)  NOT NULL,
    source           VARCHAR(20),
    external_id      VARCHAR(100),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT vacancies_pkey PRIMARY KEY (id),
    CONSTRAINT vacancies_organization_fk
        FOREIGN KEY (organization_id)
        REFERENCES public.organizations (id) ON DELETE RESTRICT,
    CONSTRAINT vacancies_created_by_fk
        FOREIGN KEY (created_by)
        REFERENCES public.users (id) ON DELETE RESTRICT
);

CREATE TABLE public.candidates (
    id               UUID         NOT NULL DEFAULT gen_random_uuid(),
    organization_id  UUID         NOT NULL,
    full_name        VARCHAR(255) NOT NULL,
    email            VARCHAR(320),
    phone            VARCHAR(30),
    source           VARCHAR(20),
    external_id      VARCHAR(100),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT candidates_pkey PRIMARY KEY (id),
    CONSTRAINT candidates_organization_fk
        FOREIGN KEY (organization_id)
        REFERENCES public.organizations (id) ON DELETE RESTRICT
);

CREATE TABLE public.resumes (
    id            UUID         NOT NULL DEFAULT gen_random_uuid(),
    candidate_id  UUID         NOT NULL,
    resume_text   TEXT,
    skills        JSONB,
    source        VARCHAR(20),
    external_id   VARCHAR(100),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT resumes_pkey PRIMARY KEY (id),
    -- Этот составной ключ нужен для проверки владельца резюме в отклике.
    CONSTRAINT resumes_id_candidate_key UNIQUE (id, candidate_id),
    CONSTRAINT resumes_candidate_fk
        FOREIGN KEY (candidate_id)
        REFERENCES public.candidates (id) ON DELETE CASCADE
);

CREATE TABLE public.applications (
    id            UUID        NOT NULL DEFAULT gen_random_uuid(),
    vacancy_id    UUID        NOT NULL,
    candidate_id  UUID        NOT NULL,
    resume_id     UUID        NOT NULL,
    status        VARCHAR(30) NOT NULL,
    applied_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    hired_at      TIMESTAMPTZ,

    CONSTRAINT applications_pkey PRIMARY KEY (id),
    CONSTRAINT applications_vacancy_fk
        FOREIGN KEY (vacancy_id)
        REFERENCES public.vacancies (id) ON DELETE RESTRICT,
    CONSTRAINT applications_candidate_fk
        FOREIGN KEY (candidate_id)
        REFERENCES public.candidates (id) ON DELETE CASCADE,
    -- NO ACTION позволяет каскадно удалить кандидата вместе с его
    -- резюме и откликами, но запрещает удаление используемого резюме отдельно.
    CONSTRAINT applications_resume_candidate_fk
        FOREIGN KEY (resume_id, candidate_id)
        REFERENCES public.resumes (id, candidate_id) ON DELETE NO ACTION
);

-- 3. AI-скрининг и его проверка человеком.

CREATE TABLE public.ai_screenings (
    id              UUID         NOT NULL DEFAULT gen_random_uuid(),
    application_id  UUID         NOT NULL,
    score           NUMERIC(5,2) NOT NULL,
    explanation     TEXT         NOT NULL,
    model_name      VARCHAR(100) NOT NULL,
    screened_at     TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ai_screenings_pkey PRIMARY KEY (id),
    CONSTRAINT ai_screenings_application_fk
        FOREIGN KEY (application_id)
        REFERENCES public.applications (id) ON DELETE CASCADE
);

CREATE TABLE public.screening_audits (
    id             UUID        NOT NULL DEFAULT gen_random_uuid(),
    screening_id   UUID        NOT NULL,
    reviewer_id    UUID        NOT NULL,
    bias_detected  BOOLEAN     NOT NULL,
    comments       TEXT,
    reviewed_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT screening_audits_pkey PRIMARY KEY (id),
    CONSTRAINT screening_audits_screening_fk
        FOREIGN KEY (screening_id)
        REFERENCES public.ai_screenings (id) ON DELETE CASCADE,
    CONSTRAINT screening_audits_reviewer_fk
        FOREIGN KEY (reviewer_id)
        REFERENCES public.users (id) ON DELETE RESTRICT
);

-- 4. Интервью.

CREATE TABLE public.interviews (
    id              UUID        NOT NULL DEFAULT gen_random_uuid(),
    application_id  UUID        NOT NULL,
    interviewer_id  UUID        NOT NULL,
    scheduled_at    TIMESTAMPTZ NOT NULL,
    status          VARCHAR(30) NOT NULL,
    result          VARCHAR(30),
    notes           TEXT,

    CONSTRAINT interviews_pkey PRIMARY KEY (id),
    CONSTRAINT interviews_application_fk
        FOREIGN KEY (application_id)
        REFERENCES public.applications (id) ON DELETE CASCADE,
    CONSTRAINT interviews_interviewer_fk
        FOREIGN KEY (interviewer_id)
        REFERENCES public.users (id) ON DELETE RESTRICT
);

-- 5. Адаптация 30/60/90.

CREATE TABLE public.onboarding_plans (
    id              UUID        NOT NULL DEFAULT gen_random_uuid(),
    application_id  UUID        NOT NULL,
    approved_by     UUID,
    start_date      DATE        NOT NULL,
    status          VARCHAR(30) NOT NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT onboarding_plans_pkey PRIMARY KEY (id),
    CONSTRAINT onboarding_plans_application_key UNIQUE (application_id),
    CONSTRAINT onboarding_plans_application_fk
        FOREIGN KEY (application_id)
        REFERENCES public.applications (id) ON DELETE CASCADE,
    CONSTRAINT onboarding_plans_approved_by_fk
        FOREIGN KEY (approved_by)
        REFERENCES public.users (id) ON DELETE SET NULL
);

CREATE TABLE public.onboarding_tasks (
    id            UUID         NOT NULL DEFAULT gen_random_uuid(),
    plan_id       UUID         NOT NULL,
    stage_day     SMALLINT     NOT NULL,
    title         VARCHAR(255) NOT NULL,
    description   TEXT,
    due_date      DATE,
    is_completed  BOOLEAN      NOT NULL DEFAULT FALSE,
    completed_at  TIMESTAMPTZ,

    CONSTRAINT onboarding_tasks_pkey PRIMARY KEY (id),
    CONSTRAINT onboarding_tasks_plan_fk
        FOREIGN KEY (plan_id)
        REFERENCES public.onboarding_plans (id) ON DELETE CASCADE,
    CONSTRAINT onboarding_tasks_stage_day_check CHECK (stage_day IN (30, 60, 90))
);

-- 6. Журнал действий и запросы на удаление.

CREATE TABLE public.audit_logs (
    id               UUID        NOT NULL DEFAULT gen_random_uuid(),
    organization_id  UUID        NOT NULL,
    actor_id         UUID,
    action           VARCHAR(80) NOT NULL,
    entity_type      VARCHAR(50) NOT NULL,
    entity_id        UUID        NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT audit_logs_pkey PRIMARY KEY (id),
    CONSTRAINT audit_logs_organization_fk
        FOREIGN KEY (organization_id)
        REFERENCES public.organizations (id) ON DELETE RESTRICT,
    CONSTRAINT audit_logs_actor_fk
        FOREIGN KEY (actor_id)
        REFERENCES public.users (id) ON DELETE SET NULL
);

CREATE TABLE public.deletion_requests (
    id               UUID        NOT NULL DEFAULT gen_random_uuid(),
    organization_id  UUID        NOT NULL,
    candidate_id     UUID,
    requested_by     UUID,
    status           VARCHAR(30) NOT NULL,
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at     TIMESTAMPTZ,

    CONSTRAINT deletion_requests_pkey PRIMARY KEY (id),
    CONSTRAINT deletion_requests_organization_fk
        FOREIGN KEY (organization_id)
        REFERENCES public.organizations (id) ON DELETE RESTRICT,
    CONSTRAINT deletion_requests_candidate_fk
        FOREIGN KEY (candidate_id)
        REFERENCES public.candidates (id) ON DELETE SET NULL,
    CONSTRAINT deletion_requests_requested_by_fk
        FOREIGN KEY (requested_by)
        REFERENCES public.users (id) ON DELETE SET NULL
);

-- Индексы на внешних ключах и основных выборках. Для PK и UNIQUE
-- PostgreSQL уже создаёт индексы автоматически.

CREATE INDEX users_organization_idx
    ON public.users (organization_id);

CREATE INDEX vacancies_organization_status_idx
    ON public.vacancies (organization_id, status);
CREATE INDEX vacancies_created_by_idx
    ON public.vacancies (created_by);

CREATE INDEX candidates_organization_idx
    ON public.candidates (organization_id);

CREATE INDEX resumes_candidate_idx
    ON public.resumes (candidate_id);

CREATE INDEX applications_vacancy_status_idx
    ON public.applications (vacancy_id, status);
CREATE INDEX applications_candidate_idx
    ON public.applications (candidate_id);
CREATE INDEX applications_resume_candidate_idx
    ON public.applications (resume_id, candidate_id);

CREATE INDEX ai_screenings_application_time_idx
    ON public.ai_screenings (application_id, screened_at DESC);

CREATE INDEX screening_audits_screening_idx
    ON public.screening_audits (screening_id);
CREATE INDEX screening_audits_reviewer_idx
    ON public.screening_audits (reviewer_id);

CREATE INDEX interviews_application_time_idx
    ON public.interviews (application_id, scheduled_at);
CREATE INDEX interviews_interviewer_time_idx
    ON public.interviews (interviewer_id, scheduled_at);

CREATE INDEX onboarding_plans_approved_by_idx
    ON public.onboarding_plans (approved_by);

CREATE INDEX onboarding_tasks_plan_stage_idx
    ON public.onboarding_tasks (plan_id, stage_day);

CREATE INDEX audit_logs_organization_time_idx
    ON public.audit_logs (organization_id, created_at DESC);
CREATE INDEX audit_logs_actor_idx
    ON public.audit_logs (actor_id);

CREATE INDEX deletion_requests_organization_status_idx
    ON public.deletion_requests (organization_id, status);
CREATE INDEX deletion_requests_candidate_idx
    ON public.deletion_requests (candidate_id);
CREATE INDEX deletion_requests_requested_by_idx
    ON public.deletion_requests (requested_by);

-- Комментарии сохраняются в самой БД и видны в клиентах PostgreSQL.

COMMENT ON TABLE public.organizations IS
    'Компании-клиенты SaaS-сервиса AI-ассистент HR.';
COMMENT ON TABLE public.users IS
    'Пользователи организации: HR-специалисты и другие участники процесса.';
COMMENT ON TABLE public.vacancies IS
    'Вакансии организации, включая записи из hh.ru и SuperJob.';
COMMENT ON TABLE public.candidates IS
    'Карточки кандидатов организации с контактными данными и источником.';
COMMENT ON TABLE public.resumes IS
    'Резюме кандидатов; skills хранит извлечённые навыки в JSONB.';
COMMENT ON TABLE public.applications IS
    'Отклики: связь вакансии, кандидата и принадлежащего ему резюме.';
COMMENT ON TABLE public.ai_screenings IS
    'Результаты AI-скрининга отклика; повторные оценки допускаются.';
COMMENT ON TABLE public.screening_audits IS
    'Проверки AI-оценки человеком, включая оценку признаков предвзятости.';
COMMENT ON TABLE public.interviews IS
    'Собеседования по отклику; на один отклик возможно несколько встреч.';
COMMENT ON TABLE public.onboarding_plans IS
    'Планы адаптации; для одного отклика допускается не более одного плана.';
COMMENT ON TABLE public.onboarding_tasks IS
    'Задачи плана адаптации на этапах 30, 60 и 90 дней.';
COMMENT ON TABLE public.audit_logs IS
    'Журнал действий. Заполняется приложением; entity_id не является FK.';
COMMENT ON TABLE public.deletion_requests IS
    'Запросы на удаление данных; сохраняются после удаления кандидата.';

COMMENT ON COLUMN public.users.role IS
    'Роль пользователя. Список допустимых значений определяет приложение.';
COMMENT ON COLUMN public.resumes.skills IS
    'Извлечённые навыки. Структура JSON в исходной ER-модели не задана.';
COMMENT ON COLUMN public.applications.resume_id IS
    'Резюме обязано принадлежать candidate_id; проверяется составным FK.';
COMMENT ON COLUMN public.ai_screenings.score IS
    'Оценка NUMERIC(5,2). Шкала оценивания в исходной ER-модели не задана.';
COMMENT ON COLUMN public.ai_screenings.explanation IS
    'Объяснение AI-оценки для интерпретации и проверки результата.';
COMMENT ON COLUMN public.screening_audits.bias_detected IS
    'Результат проверки человеком: выявлены ли признаки предвзятости.';
COMMENT ON COLUMN public.onboarding_tasks.stage_day IS
    'Этап адаптации: только 30, 60 или 90 дней.';
COMMENT ON COLUMN public.audit_logs.entity_id IS
    'UUID объекта; таблицу объекта определяет entity_type. Проверяет приложение.';
COMMENT ON COLUMN public.deletion_requests.candidate_id IS
    'Может быть NULL; при удалении кандидата автоматически обнуляется.';

COMMIT;
