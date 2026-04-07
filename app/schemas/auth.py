from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from typing_extensions import Annotated

from app.services.auth import normalize_and_validate_email, normalize_person_name, normalize_phone_number

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
EmailAddress = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=320)]
PhoneNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=7, max_length=20)]
PasswordText = Annotated[str, StringConstraints(min_length=8, max_length=256)]


class UserRole(str, Enum):
    ADMIN = "admin"
    STUDENT = "student"


class UserAccessStatus(str, Enum):
    ACTIVE = "active"
    PENDING_CREDENTIALS = "pending_credentials"


class PaymentStatus(str, Enum):
    UNCONFIRMED = "unconfirmed"
    CONFIRMED = "confirmed"


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: PersonName
    email: EmailAddress
    password: PasswordText

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        return normalize_person_name(value, field_name="name")

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_and_validate_email(value)


class PaidRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: PersonName
    father_name: PersonName
    mother_name: PersonName
    mobile_number: PhoneNumber
    email: EmailAddress

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        return normalize_person_name(value, field_name="name")

    @field_validator("father_name")
    @classmethod
    def validate_father_name(cls, value: str) -> str:
        return normalize_person_name(value, field_name="father's name")

    @field_validator("mother_name")
    @classmethod
    def validate_mother_name(cls, value: str) -> str:
        return normalize_person_name(value, field_name="mother's name")

    @field_validator("mobile_number")
    @classmethod
    def validate_mobile_number(cls, value: str) -> str:
        return normalize_phone_number(value)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_and_validate_email(value)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailAddress
    password: PasswordText

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_and_validate_email(value)


class UserSession(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str
    email: str
    full_name: str
    role: UserRole
    payment_status: PaymentStatus = PaymentStatus.CONFIRMED
    access_status: UserAccessStatus = UserAccessStatus.ACTIVE


class AuthResponse(BaseModel):
    user: UserSession
    expires_at: int = Field(gt=0)
