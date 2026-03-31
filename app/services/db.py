import logging
from typing import TypeAlias

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base, User
from app.services.auth import hash_password, normalize_email
from app.utils.time import utc_now_epoch

logger = logging.getLogger(__name__)

DatabaseSessionFactory: TypeAlias = sessionmaker[Session]

_DEFAULT_ADMIN_PASSWORD = "change-me-admin"


def build_database_url() -> str:
    return settings.sqlalchemy_database_url


def create_db_engine() -> Engine:
    connect_args = {"connect_timeout": settings.postgres_connect_timeout}
    if settings.postgres_sslmode:
        connect_args["sslmode"] = settings.postgres_sslmode
    if settings.postgres_sslrootcert:
        connect_args["sslrootcert"] = settings.postgres_sslrootcert

    return create_engine(
        build_database_url(),
        pool_pre_ping=True,
        pool_size=settings.postgres_pool_size,
        max_overflow=settings.postgres_max_overflow,
        pool_recycle=settings.postgres_pool_recycle,
        pool_timeout=30,
        connect_args=connect_args,
    )


def create_session_factory(engine: Engine | None = None) -> DatabaseSessionFactory:
    bind = engine or create_db_engine()
    return sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)


def create_db_pool() -> DatabaseSessionFactory:
    # Compatibility wrapper for existing startup/tests while the repo moves off the old pool naming.
    return create_session_factory()


def initialize_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)

    statements = [
        "ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'published';",
        "ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS availability_start_at BIGINT;",
        "ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS availability_end_at BIGINT;",
        "UPDATE quizzes SET lifecycle_status = 'published' WHERE lifecycle_status IS NULL OR lifecycle_status = '';",
        "UPDATE quizzes SET is_published = (lifecycle_status = 'published');",
        "ALTER TABLE results ADD COLUMN IF NOT EXISTS attempt_id TEXT;",
        "ALTER TABLE results ADD COLUMN IF NOT EXISTS percentage DOUBLE PRECISION;",
        "ALTER TABLE results ADD COLUMN IF NOT EXISTS submission_id TEXT;",
        "CREATE INDEX IF NOT EXISTS idx_users_role_created_at ON users(role, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_quizzes_lifecycle_created_at ON quizzes(lifecycle_status, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_quizzes_availability_window ON quizzes(availability_start_at, availability_end_at);",
        "CREATE INDEX IF NOT EXISTS idx_attempts_status_started_at ON attempts(status, started_at DESC);",
    ]

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def bootstrap_admin(session_factory: DatabaseSessionFactory) -> None:
    email = normalize_email(settings.bootstrap_admin_email)
    password = settings.bootstrap_admin_password
    if not email or not password:
        logger.info("Bootstrap admin skipped because credentials are not configured")
        return

    if password == _DEFAULT_ADMIN_PASSWORD:
        logger.warning(
            "⚠️  SECURITY WARNING: Admin account is using the default password '%s'. "
            "Set BOOTSTRAP_ADMIN_PASSWORD to a strong password before deploying to production!",
            _DEFAULT_ADMIN_PASSWORD,
        )

    with session_factory.begin() as session:
        existing = session.query(User.user_id).filter(User.email == email).first()
        if existing:
            return

        session.add(
            User(
                user_id=f"admin-{utc_now_epoch()}",
                email=email,
                full_name=settings.bootstrap_admin_name,
                password_hash=hash_password(password),
                role="admin",
                created_at=utc_now_epoch(),
            )
        )
        logger.info("Bootstrapped admin user %s", email)
