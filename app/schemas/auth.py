import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import Annotated

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
EmailAddress = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=320)]
PhoneNumber = Annotated[str, StringConstraints(strip_whitespace=True, min_length=7, max_length=20)]
PasswordText = Annotated[str, StringConstraints(min_length=8, max_length=256)]

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
_PHONE_RE = re.compile(r"^[0-9+\-\s()]{7,20}$")


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

    @model_validator(mode="after")
    def validate_email(self) -> "RegisterRequest":
        if not _EMAIL_RE.match(self.email):
            raise ValueError("email must be a valid address (e.g. user@example.com)")
        return self


class PaidRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: PersonName
    father_name: PersonName
    mother_name: PersonName
    mobile_number: PhoneNumber
    email: EmailAddress

    @model_validator(mode="after")
    def validate_fields(self) -> "PaidRegistrationRequest":
        if not _EMAIL_RE.match(self.email):
            raise ValueError("email must be a valid address (e.g. user@example.com)")
        if not _PHONE_RE.match(self.mobile_number):
            raise ValueError("mobile number must contain only digits or common phone symbols")
        return self


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailAddress
    password: PasswordText

    @model_validator(mode="after")
    def validate_email(self) -> "LoginRequest":
        if not _EMAIL_RE.match(self.email):
            raise ValueError("email must be a valid address (e.g. user@example.com)")
        return self


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
