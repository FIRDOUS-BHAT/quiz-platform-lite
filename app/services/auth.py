import base64
import hashlib
import hmac
import os
import re
import secrets

_EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$")
_PHONE_INPUT_RE = re.compile(r"^[0-9+\-\s()]+$")
_NAME_ALLOWED_PUNCTUATION = {" ", ".", "'", "-"}


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_and_validate_email(email: str, *, field_name: str = "email") -> str:
    normalized = normalize_email(email)
    if not _EMAIL_RE.match(normalized):
        raise ValueError(f"{field_name} must be a valid address (e.g. user@example.com)")
    return normalized


def normalize_person_name(name: str, *, field_name: str = "name") -> str:
    normalized = " ".join(name.strip().split())
    if len(normalized) < 2:
        raise ValueError(f"{field_name} must be at least 2 characters long")
    if len(normalized) > 256:
        raise ValueError(f"{field_name} must be 256 characters or fewer")
    if not any(char.isalpha() for char in normalized):
        raise ValueError(f"{field_name} must include at least one letter")
    if normalized[0] in _NAME_ALLOWED_PUNCTUATION or normalized[-1] in _NAME_ALLOWED_PUNCTUATION:
        raise ValueError(f"{field_name} must start and end with a letter")
    if any(not (char.isalpha() or char in _NAME_ALLOWED_PUNCTUATION) for char in normalized):
        raise ValueError(
            f"{field_name} can only contain letters, spaces, apostrophes, periods, and hyphens"
        )
    return normalized


def normalize_phone_number(value: str, *, field_name: str = "mobile number") -> str:
    normalized = value.strip()
    if not _PHONE_INPUT_RE.match(normalized):
        raise ValueError(f"{field_name} can only contain digits, spaces, parentheses, hyphens, and an optional +")
    if normalized.count("+") > 1 or ("+" in normalized and not normalized.startswith("+")):
        raise ValueError(f"{field_name} can only use + at the beginning")
    digits_only = re.sub(r"\D", "", normalized)
    if not 10 <= len(digits_only) <= 15:
        raise ValueError(f"{field_name} must contain 10 to 15 digits")
    return digits_only


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390_000)
    return "pbkdf2_sha256$390000$%s$%s" % (
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_b64, hash_b64 = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(hash_b64.encode("ascii"))
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations_text),
        )
        return hmac.compare_digest(derived, expected)
    except Exception:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
