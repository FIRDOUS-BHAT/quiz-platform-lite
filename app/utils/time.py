import time
from datetime import date, datetime, timezone
from datetime import tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings


def utc_now_epoch() -> int:
    return int(time.time())


def local_timezone() -> tzinfo:
    """Return the configured app timezone, falling back to system local and then UTC."""
    configured_timezone = (settings.app_timezone or "").strip()
    if configured_timezone:
        try:
            return ZoneInfo(configured_timezone)
        except ZoneInfoNotFoundError:
            pass
    tz = datetime.now().astimezone().tzinfo
    return tz if tz is not None else timezone.utc


def local_timezone_name() -> str:
    configured_timezone = (settings.app_timezone or "").strip()
    if configured_timezone:
        return configured_timezone
    tz = local_timezone()
    return datetime.now(tz).tzname() or str(tz) or "Local time"


def epoch_to_local_datetime(value: int | None) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        instant = datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return instant.astimezone(local_timezone())


def epoch_to_local_iso(value: int | None) -> str | None:
    local = epoch_to_local_datetime(value)
    return local.isoformat(timespec="seconds") if local is not None else None


def coerce_epoch(value: object, *, field_name: str) -> int:
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=local_timezone())
        return int(aware.timestamp())
    if isinstance(value, date):
        local = datetime(value.year, value.month, value.day, tzinfo=local_timezone())
        return int(local.timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} must not be empty")
        if normalized.isdigit():
            return int(normalized)
        try:
            if normalized.endswith("Z"):
                normalized = normalized[:-1] + "+00:00"
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an epoch or ISO datetime") from exc
        aware = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=local_timezone())
        return int(aware.timestamp())
    raise ValueError(f"{field_name} must be an epoch or ISO datetime")
