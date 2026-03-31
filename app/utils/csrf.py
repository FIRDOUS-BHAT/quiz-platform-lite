"""CSRF protection using the double-submit cookie pattern.

How it works:
1. On GET requests, a CSRF token is generated and set as a cookie (`csrf_token`)
2. On POST/PUT/PATCH/DELETE requests, the token from the form field `csrf_token`
   must match the token from the cookie
3. The token is HMAC-signed with the server's secret key to prevent forgery
"""
import hashlib
import hmac
import logging
import secrets
import time

from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

logger = logging.getLogger(__name__)

_CSRF_COOKIE_NAME = "csrf_token"
_CSRF_FORM_FIELD = "csrf_token"
_CSRF_HEADER_NAME = "x-csrf-token"
_TOKEN_TTL_SECONDS = 60 * 60 * 12  # 12 hours

# Paths exempt from CSRF validation (API endpoints that use Bearer auth, not cookies)
_EXEMPT_PATH_PREFIXES = (
    "/quiz/",
    "/health",
    "/metrics",
    "/docs",
    "/openapi.json",
)


def _sign_token(raw_token: str) -> str:
    """HMAC-sign a token with the server secret."""
    return hmac.new(
        settings.csrf_secret_key.encode("utf-8"),
        raw_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_csrf_token() -> str:
    """Generate a signed CSRF token."""
    raw = f"{secrets.token_urlsafe(24)}:{int(time.time())}"
    signature = _sign_token(raw)
    return f"{raw}:{signature}"


def validate_csrf_token(token: str) -> bool:
    """Validate a signed CSRF token."""
    if not token:
        return False
    parts = token.rsplit(":", 2)
    if len(parts) != 3:
        return False
    raw = f"{parts[0]}:{parts[1]}"
    expected_signature = _sign_token(raw)
    if not hmac.compare_digest(parts[2], expected_signature):
        return False
    try:
        created_at = int(parts[1])
        if time.time() - created_at > _TOKEN_TTL_SECONDS:
            return False
    except (ValueError, TypeError):
        return False
    return True


def _is_exempt(path: str) -> bool:
    """Check if a path is exempt from CSRF validation."""
    return any(path.startswith(prefix) for prefix in _EXEMPT_PATH_PREFIXES)


def get_csrf_token(request: Request) -> str:
    """Get or generate CSRF token for a request (used in templates)."""
    existing = request.cookies.get(_CSRF_COOKIE_NAME, "")
    if existing and validate_csrf_token(existing):
        return existing
    return generate_csrf_token()


def set_csrf_cookie(response: Response, token: str) -> None:
    """Set the CSRF cookie on a response."""
    response.set_cookie(
        _CSRF_COOKIE_NAME,
        token,
        httponly=False,  # Must be readable by JavaScript for AJAX requests
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
        max_age=_TOKEN_TTL_SECONDS,
    )


async def _request_csrf_token(request: Request) -> str:
    return request.headers.get(_CSRF_HEADER_NAME, "").strip()


async def check_csrf(request: Request) -> str | None:
    """
    Validate CSRF on state-changing requests.
    Returns None if valid, or an error message string if invalid.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None

    if _is_exempt(request.url.path):
        return None

    cookie_token = request.cookies.get(_CSRF_COOKIE_NAME, "")
    if not cookie_token or not validate_csrf_token(cookie_token):
        return "Invalid or missing CSRF token"

    request_token = await _request_csrf_token(request)
    if request_token and not hmac.compare_digest(request_token, cookie_token):
        return "Invalid or missing CSRF token"

    return None
