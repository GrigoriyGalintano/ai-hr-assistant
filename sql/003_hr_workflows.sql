BEGIN;

ALTER TABLE public.users ADD COLUMN password_hash TEXT;
CREATE UNIQUE INDEX users_org_login_idx ON public.users (organization_id, lower(email))
    WHERE password_hash IS NOT NULL;

CREATE TABLE public.auth_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX auth_sessions_user_idx ON public.auth_sessions(user_id);
CREATE INDEX auth_sessions_expiry_idx ON public.auth_sessions(expires_at);

CREATE TABLE public.integration_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE RESTRICT,
    provider VARCHAR(20) NOT NULL CHECK (provider IN ('hh', 'superjob')),
    access_token_encrypted TEXT,
    app_key_encrypted TEXT,
    employer_id VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (organization_id, provider)
);

ALTER TABLE public.interviews ADD COLUMN duration_minutes SMALLINT NOT NULL DEFAULT 60
    CHECK (duration_minutes BETWEEN 1 AND 480);
ALTER TABLE public.screening_audits ADD COLUMN reviewer_score NUMERIC(5,2)
    CHECK (reviewer_score BETWEEN 0 AND 100);

CREATE INDEX candidates_external_idx ON public.candidates(organization_id, source, external_id);
CREATE INDEX resumes_external_idx ON public.resumes(candidate_id, source, external_id);
CREATE INDEX vacancies_external_idx ON public.vacancies(organization_id, source, external_id);
CREATE INDEX applications_pair_idx ON public.applications(vacancy_id, candidate_id, resume_id);

INSERT INTO public.schema_migrations(version) VALUES ('003_hr_workflows');
COMMIT;
