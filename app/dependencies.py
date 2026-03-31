from typing import Literal

from fastapi import Depends, HTTPException, Request, status

from app.schemas.auth import UserSession
from app.services.db import DatabaseSessionFactory

SessionScope = Literal["student", "admin", "either"]


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


def _candidate_session_tokens(request: Request, scope: SessionScope) -> list[str]:
    tokens: list[str] = []
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token:
            tokens.append(token)

    legacy_cookie_name = getattr(request.app.state, "session_cookie_name", "quiz_platform_session")
    student_cookie_name = getattr(
        request.app.state,
        "student_session_cookie_name",
        "quiz_platform_student_session",
    )
    admin_cookie_name = getattr(
        request.app.state,
        "admin_session_cookie_name",
        "quiz_platform_admin_session",
    )

    if scope == "admin":
        cookie_names = [admin_cookie_name, legacy_cookie_name]
    elif scope == "student":
        cookie_names = [student_cookie_name, legacy_cookie_name]
    else:
        cookie_names = [admin_cookie_name, student_cookie_name, legacy_cookie_name]

    for cookie_name in cookie_names:
        token = request.cookies.get(cookie_name)
        if token and token not in tokens:
            tokens.append(token)

    return tokens


async def get_optional_current_user(
    request: Request,
    store=None,
    scope: SessionScope = "either",
) -> UserSession | None:
    session_tokens = _candidate_session_tokens(request, scope)
    if not session_tokens:
        return None
    if store is None:
        from app.services.platform_store import PlatformStore

        store = PlatformStore(get_db_session_factory(request))
    candidate_store = store
    for session_token in session_tokens:
        user = await candidate_store.get_user_by_session(session_token)
        if user is None:
            continue
        if scope == "either" or user.role.value == scope:
            return user
    return None


async def get_optional_student_user(request: Request, store=Depends(get_store)) -> UserSession | None:
    return await get_optional_current_user(request, store=store, scope="student")


async def get_optional_admin_user(request: Request, store=Depends(get_store)) -> UserSession | None:
    return await get_optional_current_user(request, store=store, scope="admin")


async def get_current_user(request: Request, store=Depends(get_store)) -> UserSession:
    user = await get_optional_current_user(request, store=store, scope="student")
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


async def get_current_admin(request: Request, store=Depends(get_store)) -> UserSession:
    user = await get_optional_current_user(request, store=store, scope="admin")
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user
