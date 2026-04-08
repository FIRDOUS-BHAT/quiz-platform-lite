import logging
import ssl
from typing import TypeAlias

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base, User
from app.services.auth import hash_password, normalize_email
from app.utils.time import utc_now_epoch

logger = logging.getLogger(__name__)

DatabaseSessionFactory: TypeAlias = async_sessionmaker[AsyncSession]

_DEFAULT_ADMIN_PASSWORD = "change-me-admin"
_SCHEMA_INIT_LOCK_ID = 814205101


def build_database_url() -> str:
    return settings.sqlalchemy_database_url


def _build_ssl_context(sslmode: str, sslrootcert: str | None) -> ssl.SSLContext | str | bool:
    normalized_mode = sslmode.strip().lower()
    if normalized_mode in {"", "disable"}:
        return False
    if normalized_mode in {"allow", "prefer"} and not sslrootcert:
        return normalized_mode

    if normalized_mode == "require" and not sslrootcert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    context = ssl.create_default_context(cafile=sslrootcert or None)
    context.check_hostname = normalized_mode == "verify-full"
    if normalized_mode == "require":
        context.check_hostname = False
    return context


def build_database_connect_args() -> dict[str, object]:
    connect_args: dict[str, object] = {"timeout": settings.postgres_connect_timeout}
    if settings.postgres_sslmode:
        connect_args["ssl"] = _build_ssl_context(settings.postgres_sslmode, settings.postgres_sslrootcert)
    return connect_args


def create_db_engine() -> AsyncEngine:
    return create_async_engine(
        build_database_url(),
        pool_pre_ping=True,
        pool_size=settings.postgres_pool_size,
        max_overflow=settings.postgres_max_overflow,
        pool_recycle=settings.postgres_pool_recycle,
        pool_timeout=30,
        connect_args=build_database_connect_args(),
    )


def create_session_factory(engine: AsyncEngine | None = None) -> DatabaseSessionFactory:
    bind = engine or create_db_engine()
    return async_sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)


def create_db_pool(engine: AsyncEngine | None = None) -> DatabaseSessionFactory:
    # Compatibility wrapper for existing startup/tests while the repo moves off the old pool naming.
    return create_session_factory(engine)


async def initialize_schema(engine: AsyncEngine) -> None:
    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS access_status TEXT NOT NULL DEFAULT 'active';",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS father_name TEXT;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS mother_name TEXT;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS mobile_number TEXT;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS payment_status TEXT NOT NULL DEFAULT 'confirmed';",
        "ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'published';",
        "ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS availability_start_at BIGINT;",
        "ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS availability_end_at BIGINT;",
        "UPDATE users SET email = lower(btrim(email)) WHERE email IS NOT NULL;",
        "UPDATE users SET full_name = btrim(regexp_replace(full_name, '[[:space:]]+', ' ', 'g')) WHERE full_name IS NOT NULL;",
        "UPDATE users SET father_name = NULLIF(btrim(regexp_replace(father_name, '[[:space:]]+', ' ', 'g')), '') WHERE father_name IS NOT NULL;",
        "UPDATE users SET mother_name = NULLIF(btrim(regexp_replace(mother_name, '[[:space:]]+', ' ', 'g')), '') WHERE mother_name IS NOT NULL;",
        "UPDATE users SET mobile_number = NULLIF(regexp_replace(mobile_number, '[^0-9]', '', 'g'), '') WHERE mobile_number IS NOT NULL;",
        "UPDATE users SET access_status = 'active' WHERE access_status IS NULL OR access_status = '';",
        "UPDATE users SET payment_status = 'confirmed' WHERE payment_status IS NULL OR payment_status = '';",
        "UPDATE quizzes SET lifecycle_status = 'published' WHERE lifecycle_status IS NULL OR lifecycle_status = '';",
        "UPDATE quizzes SET is_published = (lifecycle_status = 'published');",
        "ALTER TABLE results ADD COLUMN IF NOT EXISTS attempt_id TEXT;",
        "ALTER TABLE results ADD COLUMN IF NOT EXISTS percentage DOUBLE PRECISION;",
        "ALTER TABLE results ADD COLUMN IF NOT EXISTS submission_id TEXT;",
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_entity_created_at ON audit_logs(entity_type, entity_id, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_event_created_at ON audit_logs(event_type, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_payment_transactions_user_created_at ON payment_transactions(user_id, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_payment_transactions_status_created_at ON payment_transactions(status, created_at DESC);",
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_email_valid') THEN
                ALTER TABLE users
                ADD CONSTRAINT ck_users_email_valid
                CHECK (email = lower(btrim(email)) AND email ~ '^[a-z0-9._%+-]+@[a-z0-9.-]+[.][a-z]{2,}$') NOT VALID;
            END IF;
        END $$;
        """,
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_full_name_valid') THEN
                ALTER TABLE users
                ADD CONSTRAINT ck_users_full_name_valid
                CHECK (
                    char_length(full_name) BETWEEN 2 AND 256
                    AND full_name = btrim(full_name)
                    AND full_name !~ '[0-9]'
                    AND full_name !~ ' {2,}'
                ) NOT VALID;
            END IF;
        END $$;
        """,
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_father_name_valid') THEN
                ALTER TABLE users
                ADD CONSTRAINT ck_users_father_name_valid
                CHECK (
                    father_name IS NULL OR (
                        char_length(father_name) BETWEEN 2 AND 256
                        AND father_name = btrim(father_name)
                        AND father_name !~ '[0-9]'
                        AND father_name !~ ' {2,}'
                    )
                ) NOT VALID;
            END IF;
        END $$;
        """,
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_mother_name_valid') THEN
                ALTER TABLE users
                ADD CONSTRAINT ck_users_mother_name_valid
                CHECK (
                    mother_name IS NULL OR (
                        char_length(mother_name) BETWEEN 2 AND 256
                        AND mother_name = btrim(mother_name)
                        AND mother_name !~ '[0-9]'
                        AND mother_name !~ ' {2,}'
                    )
                ) NOT VALID;
            END IF;
        END $$;
        """,
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_mobile_number_valid') THEN
                ALTER TABLE users
                ADD CONSTRAINT ck_users_mobile_number_valid
                CHECK (mobile_number IS NULL OR mobile_number ~ '^[0-9]{10,15}$') NOT VALID;
            END IF;
        END $$;
        """,
        "CREATE INDEX IF NOT EXISTS idx_users_role_created_at ON users(role, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_users_access_status_created_at ON users(access_status, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_users_mobile_number ON users(mobile_number);",
        "CREATE INDEX IF NOT EXISTS idx_users_payment_status_created_at ON users(payment_status, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_quizzes_lifecycle_created_at ON quizzes(lifecycle_status, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_quizzes_availability_window ON quizzes(availability_start_at, availability_end_at);",
        "CREATE INDEX IF NOT EXISTS idx_attempts_status_started_at ON attempts(status, started_at DESC);",
    ]

    async with engine.begin() as connection:
        # Gunicorn workers can run startup concurrently, so serialize DDL.
        await connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": _SCHEMA_INIT_LOCK_ID})
        await connection.run_sync(Base.metadata.create_all)
        for statement in statements:
            await connection.execute(text(statement))


async def bootstrap_admin(session_factory: DatabaseSessionFactory) -> None:
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

    async with session_factory.begin() as session:
        result = await session.execute(
            insert(User)
            .values(
                user_id=f"admin-{utc_now_epoch()}",
                email=email,
                full_name=settings.bootstrap_admin_name,
                password_hash=hash_password(password),
                role="admin",
                payment_status="confirmed",
                access_status="active",
                created_at=utc_now_epoch(),
            )
            .on_conflict_do_nothing(index_elements=[User.email])
        )
        if result.rowcount:
            logger.info("Bootstrapped admin user %s", email)
