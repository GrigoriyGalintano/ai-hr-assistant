from pathlib import Path

import psycopg

from app.config import Settings

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def migrate(database_url: str) -> None:
    url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("SELECT pg_advisory_lock(20868529)")
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS public.schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            if connection.execute("SELECT to_regclass('public.vacancies')").fetchone()[0] is None:
                connection.execute((SQL_DIR / "001_initial.sql").read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO public.schema_migrations (version) VALUES ('001_initial') "
                "ON CONFLICT DO NOTHING"
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for path in sorted(SQL_DIR.glob("*.sql")):
                if path.stem not in applied:
                    connection.execute(path.read_text(encoding="utf-8"))
                    print(f"Applied {path.name}")
        finally:
            # A failed multi-statement migration can leave a transaction aborted.
            connection.execute("ROLLBACK")
            connection.execute("SELECT pg_advisory_unlock(20868529)")


if __name__ == "__main__":
    migrate(Settings().database_url)
