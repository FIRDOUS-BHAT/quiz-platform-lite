import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import Annotated

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
EmailAddress = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=320)]
PasswordText = Annotated[str, StringConstraints(min_length=8, max_length=256)]

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class UserRole(str, Enum):
    ADMIN = "admin"
    STUDENT = "student"


class UserAccessStatus(str, Enum):
    ACTIVE = "active"
    PENDING_CREDENTIALS = "pending_credentials"


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: Identifier
    email: EmailAddress
    password: PasswordText

    @model_validator(mode="after")
    def validate_email(self) -> "RegisterRequest":
        if not _EMAIL_RE.match(self.email):
            raise ValueError("email must be a valid address (e.g. user@example.com)")
        return self


class PaidRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: Identifier
    email: EmailAddress

    @model_validator(mode="after")
    def validate_email(self) -> "PaidRegistrationRequest":
        if not _EMAIL_RE.match(self.email):
            raise ValueError("email must be a valid address (e.g. user@example.com)")
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
    access_status: UserAccessStatus = UserAccessStatus.ACTIVE


class AuthResponse(BaseModel):
    user: UserSession
    expires_at: int = Field(gt=0)
