import argparse
from getpass import getpass
from uuid import UUID

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.hr_schemas import UserCreate
from app.models import Organization, User
from app.security import hash_password


def main():
    parser = argparse.ArgumentParser(
        description="Create an administrator in an existing organization"
    )
    parser.add_argument("--organization-id", type=UUID, required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    password = getpass("Password (12-128 characters): ")
    if password != getpass("Repeat password: "):
        parser.exit(1, "Passwords do not match.\n")
    body = UserCreate(full_name=args.name, email=args.email, password=password, role="admin")
    engine = create_engine(Settings().database_url)
    try:
        with Session(engine) as session:
            organization = session.scalar(
                select(Organization)
                .where(Organization.id == args.organization_id)
                .with_for_update()
            )
            if organization is None:
                parser.exit(1, "Organization not found.\n")
            if session.scalar(
                select(User.id).where(
                    User.organization_id == organization.id,
                    func.lower(User.email) == str(body.email).lower(),
                )
            ):
                parser.exit(1, "This email already exists in the organization.\n")
            user = User(
                organization_id=organization.id,
                full_name=body.full_name,
                email=str(body.email).lower(),
                password_hash=hash_password(body.password.get_secret_value()),
                role="admin",
            )
            session.add(user)
            session.commit()
            print(f"Administrator created: {user.id}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
