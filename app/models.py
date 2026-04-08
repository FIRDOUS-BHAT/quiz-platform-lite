from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    father_name: Mapped[str | None] = mapped_column(String(256))
    mother_name: Mapped[str | None] = mapped_column(String(256))
    mobile_number: Mapped[str | None] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_status: Mapped[str] = mapped_column(String(32), nullable=False, default="confirmed", index=True)
    access_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    sessions: Mapped[list["SessionToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    attempts: Mapped[list["Attempt"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    quizzes_created: Mapped[list["Quiz"]] = relationship(back_populates="creator")
    payment_transactions: Mapped[list["PaymentTransaction"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'student')", name="ck_users_role"),
        CheckConstraint(
            "email = lower(btrim(email)) AND email ~ '^[a-z0-9._%+-]+@[a-z0-9.-]+[.][a-z]{2,}$'",
            name="ck_users_email_valid",
        ),
        CheckConstraint(
            "char_length(full_name) BETWEEN 2 AND 256 "
            "AND full_name = btrim(full_name) "
            "AND full_name !~ '[0-9]' "
            "AND full_name !~ ' {2,}'",
            name="ck_users_full_name_valid",
        ),
        CheckConstraint(
            "father_name IS NULL OR ("
            "char_length(father_name) BETWEEN 2 AND 256 "
            "AND father_name = btrim(father_name) "
            "AND father_name !~ '[0-9]' "
            "AND father_name !~ ' {2,}'"
            ")",
            name="ck_users_father_name_valid",
        ),
        CheckConstraint(
            "mother_name IS NULL OR ("
            "char_length(mother_name) BETWEEN 2 AND 256 "
            "AND mother_name = btrim(mother_name) "
            "AND mother_name !~ '[0-9]' "
            "AND mother_name !~ ' {2,}'"
            ")",
            name="ck_users_mother_name_valid",
        ),
        CheckConstraint(
            "mobile_number IS NULL OR mobile_number ~ '^[0-9]{10,15}$'",
            name="ck_users_mobile_number_valid",
        ),
        CheckConstraint("payment_status IN ('unconfirmed', 'confirmed')", name="ck_users_payment_status"),
        CheckConstraint("access_status IN ('active', 'pending_credentials')", name="ck_users_access_status"),
    )


class SessionToken(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    user: Mapped[User] = relationship(back_populates="sessions")


class Quiz(Base):
    __tablename__ = "quizzes"

    quiz_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False, default="published", index=True)
    availability_start_at: Mapped[int | None] = mapped_column(BigInteger, index=True)
    availability_end_at: Mapped[int | None] = mapped_column(BigInteger, index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.user_id", ondelete="SET NULL"))
    source_filename: Mapped[str | None] = mapped_column(String(512))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    creator: Mapped[User | None] = relationship(back_populates="quizzes_created")
    attempts: Mapped[list["Attempt"]] = relationship(back_populates="quiz", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("lifecycle_status IN ('draft', 'published', 'archived')", name="ck_quizzes_lifecycle_status"),
    )


class Attempt(Base):
    __tablename__ = "attempts"

    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    quiz_id: Mapped[str] = mapped_column(ForeignKey("quizzes.quiz_id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    started_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    submitted_at: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    answers: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB)

    quiz: Mapped[Quiz] = relationship(back_populates="attempts")
    user: Mapped[User] = relationship(back_populates="attempts")

    __table_args__ = (
        UniqueConstraint("quiz_id", "user_id", name="uq_attempts_quiz_user"),
        CheckConstraint("status IN ('active', 'submitted', 'scored', 'expired')", name="ck_attempts_status"),
    )


class Result(Base):
    __tablename__ = "results"

    quiz_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True, index=True)
    attempt_id: Mapped[str | None] = mapped_column(String(128))
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    percentage: Mapped[float] = mapped_column(Float, nullable=False)
    evaluated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    submission_id: Mapped[str | None] = mapped_column(String(128))
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    audit_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(128), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    payment_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_txn_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), index=True)
    amount: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="initiated", index=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    raw_request: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    completed_at: Mapped[int | None] = mapped_column(BigInteger, index=True)

    user: Mapped[User] = relationship(back_populates="payment_transactions")

    __table_args__ = (
        CheckConstraint("provider IN ('payu')", name="ck_payment_transactions_provider"),
        CheckConstraint(
            "status IN ('initiated', 'success', 'failure', 'tampered')",
            name="ck_payment_transactions_status",
        ),
        CheckConstraint("amount ~ '^[0-9]+([.][0-9]{2})?$'", name="ck_payment_transactions_amount"),
    )
