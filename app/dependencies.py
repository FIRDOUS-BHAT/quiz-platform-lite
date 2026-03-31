from fastapi import Depends, HTTPException, Request, status

from app.schemas.auth import UserRole, UserSession
from app.services.db import DatabaseSessionFactory


def get_db_session_factory(request: Request) -> DatabaseSessionFactory:
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if session_factory is None:
        session_factory = getattr(request.app.state, "db_pool", None)
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database pool not initialized",
        )
    return session_factory


def get_store(request: Request):
    from app.services.platform_store import PlatformStore

    return PlatformStore(get_db_session_factory(request))


def _resolve_session_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.cookies.get(getattr(request.app.state, "session_cookie_name", "quiz_platform_session"))


def get_optional_current_user(
    request: Request,
    store=None,
) -> UserSession | None:
    session_token = _resolve_session_token(request)
    if not session_token:
        return None
    if store is None:
        from app.services.platform_store import PlatformStore

        store = PlatformStore(get_db_session_factory(request))
    candidate_store = store
    return candidate_store.get_user_by_session(session_token)


def get_current_user(request: Request, store=Depends(get_store)) -> UserSession:
    user = get_optional_current_user(request, store=store)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


def get_current_admin(request: Request, store=Depends(get_store)) -> UserSession:
    user = get_current_user(request, store=store)
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
