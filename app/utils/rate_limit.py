import logging
import threading
import time

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)
_COUNTS: dict[str, int] = {}
_COUNTS_LOCK = threading.Lock()


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind a reverse proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_key(prefix: str, ip: str) -> str:
    window = int(time.time()) // 60  # 1-minute window
    return f"ratelimit:{prefix}:{ip}:{window}"


def _prune_expired_keys(current_window: int) -> None:
    expired_suffix = f":{current_window - 2}"
    for key in list(_COUNTS):
        if key.endswith(expired_suffix):
            _COUNTS.pop(key, None)


def check_rate_limit(
    request: Request,
    prefix: str,
    max_requests: int,
) -> None:
    ip = _get_client_ip(request)
    key = _rate_limit_key(prefix, ip)
    current_window = int(time.time()) // 60
    with _COUNTS_LOCK:
        _prune_expired_keys(current_window)
        current = _COUNTS.get(key, 0) + 1
        _COUNTS[key] = current

    if current > max_requests:
        logger.warning("Rate limit exceeded for %s on %s (count=%d, limit=%d)", ip, prefix, current, max_requests)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )


def rate_limit_login(request: Request) -> None:
    """Rate limit dependency for login endpoints."""
    from app.config import settings
    check_rate_limit(request, "login", settings.rate_limit_login)


def rate_limit_register(request: Request) -> None:
    """Rate limit dependency for registration endpoints."""
    from app.config import settings
    check_rate_limit(request, "register", settings.rate_limit_register)


def rate_limit_api(request: Request) -> None:
    """Rate limit dependency for general API endpoints."""
    from app.config import settings
    check_rate_limit(request, "api", settings.rate_limit_api)
