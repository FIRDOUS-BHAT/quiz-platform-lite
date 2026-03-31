import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.models import User
from app.schemas.quiz import QuizDefinition, QuizLifecycleStatus
from app.services.auth import normalize_email
from app.services.db import bootstrap_admin, create_db_engine, create_session_factory, initialize_schema
from app.services.platform_store import PlatformStore


def load_quiz(file_path: str, quiz_id: str) -> QuizDefinition:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            quiz_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: File {file_path} not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {file_path}: {e}")
        sys.exit(1)

    try:
        return QuizDefinition.model_validate({**quiz_data, "quiz_id": quiz_id})
    except Exception as exc:
        print(f"Error: Invalid quiz schema: {exc}")
        sys.exit(1)


def seed_quiz(file_path: str, quiz_id: str, lifecycle_status: QuizLifecycleStatus) -> None:
    quiz = load_quiz(file_path, quiz_id)
    engine = create_db_engine()
    try:
        initialize_schema(engine)
        session_factory = create_session_factory(engine)
        bootstrap_admin(session_factory)

        with session_factory() as session:
            admin = session.execute(
                select(User).where(User.email == normalize_email(settings.bootstrap_admin_email))
            ).scalar_one_or_none()

        if admin is None:
            print("Error: bootstrap admin account was not found.")
            sys.exit(1)

        created = PlatformStore(session_factory).create_quiz(
            quiz,
            created_by=admin.user_id,
            source_filename=Path(file_path).name,
            lifecycle_status=lifecycle_status,
        )
        print(f"Seeded quiz '{created['title']}' as id '{created['quiz_id']}'.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed quiz data into PostgreSQL")
    parser.add_argument("--file", required=True, help="Path to quiz JSON file")
    parser.add_argument("--id", required=True, help="Quiz ID")
    parser.add_argument(
        "--status",
        choices=("draft", "published", "archived"),
        default="published",
        help="Initial lifecycle status",
    )

    args = parser.parse_args()
    seed_quiz(args.file, args.id, args.status)
