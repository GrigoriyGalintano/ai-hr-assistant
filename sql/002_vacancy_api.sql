BEGIN;

ALTER TABLE public.vacancies
    ADD COLUMN description TEXT,
    ADD COLUMN responsibilities TEXT,
    ADD COLUMN department VARCHAR(255),
    ADD COLUMN location VARCHAR(255),
    ADD COLUMN employment_type VARCHAR(50),
    ADD COLUMN experience VARCHAR(255),
    ADD COLUMN salary_from NUMERIC(14,2),
    ADD COLUMN salary_to NUMERIC(14,2),
    ADD COLUMN currency VARCHAR(3),
    ADD COLUMN updated_at TIMESTAMPTZ;

-- Preserve existing vacancies when upgrading the original ER schema.
UPDATE public.vacancies
SET description = COALESCE(NULLIF(requirements, ''), title), updated_at = created_at;

ALTER TABLE public.vacancies
    ALTER COLUMN description SET NOT NULL,
    ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP,
    ALTER COLUMN updated_at SET NOT NULL,
    ADD CONSTRAINT vacancies_salary_nonnegative CHECK (
        (salary_from IS NULL OR salary_from >= 0)
        AND (salary_to IS NULL OR salary_to >= 0)
    ),
    ADD CONSTRAINT vacancies_salary_range CHECK (
        salary_from IS NULL OR salary_to IS NULL OR salary_from <= salary_to
    );

CREATE INDEX vacancies_organization_status_created_idx
    ON public.vacancies (organization_id, status, created_at DESC, id);

INSERT INTO public.schema_migrations (version) VALUES ('002_vacancy_api');
COMMIT;
