from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Organization, User

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000002")


def main():
    settings = Settings()
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            if session.get(Organization, ORG_ID) is None:
                session.add(Organization(id=ORG_ID, name="Development"))
                session.flush()
            if session.get(User, USER_ID) is None:
                session.add(
                    User(
                        id=USER_ID,
                        organization_id=ORG_ID,
                        full_name="Local HR",
                        email="hr@example.test",
                        role="hr",
                    )
                )
            session.commit()
        print(f"Development user: {USER_ID}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
