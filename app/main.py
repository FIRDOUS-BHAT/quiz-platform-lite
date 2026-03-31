import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict
from urllib.parse import urlencode

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.middleware import LimitUploadSize
from app.routers import quiz, result, submission, web
from app.services.db import bootstrap_admin, create_db_engine, create_db_pool, initialize_schema
from app.utils.csrf import check_csrf, get_csrf_token, set_csrf_cookie
from app.utils.request_id import RequestIdMiddleware

# Configure logging — JSON in production, plain text in development
if settings.is_production:
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            log = {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                log["exception"] = self.formatException(record.exc_info)
            return json.dumps(log)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

logger = logging.getLogger(__name__)


def _auth_redirect_response(request: Request, exc: StarletteHTTPException) -> RedirectResponse | None:
    if exc.status_code != status.HTTP_401_UNAUTHORIZED:
        return None
    if "text/html" not in request.headers.get("accept", ""):
        return None

    if request.url.path.startswith("/app/admin"):
        target = "/app/admin/login"
    elif request.url.path.startswith("/app/"):
        target = "/app/login"
    else:
        return None

    query = urlencode({"message": str(exc.detail), "message_type": "error"})
    return RedirectResponse(url=f"{target}?{query}", status_code=status.HTTP_303_SEE_OTHER)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if settings.secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    """CSRF protection via double-submit cookie pattern."""
    async def dispatch(self, request: Request, call_next):
        # Validate CSRF on state-changing requests
        error = await check_csrf(request)
        if error is not None:
            content_type = request.headers.get("accept", "")
            if "text/html" in content_type:
                from fastapi.responses import RedirectResponse
                referer = request.headers.get("referer", "/app")
                return RedirectResponse(url=referer, status_code=303)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": error},
            )

        response = await call_next(request)

        # Inject CSRF cookie on HTML responses so templates can read it
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            token = get_csrf_token(request)
            set_csrf_cookie(response, token)

        return response


async def check_postgres_connection(engine: AsyncEngine | None = None) -> bool:
    """Check if Postgres is reachable."""
    try:
        managed_engine = engine or create_db_engine()
        async with managed_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        if engine is None:
            await managed_engine.dispose()
        return True
    except Exception as exc:
        logger.error("Postgres connection failed: %s", exc)
        return False


async def build_dependency_report(app: FastAPI) -> Dict[str, Any]:
    postgres_ok = await check_postgres_connection(getattr(app.state, "db_engine", None))

    return {
        "postgres": {
            "status": "connected" if postgres_ok else "disconnected",
            "host": settings.postgres_host,
            "port": settings.postgres_port,
        },
    }


def dependency_http_status(report: Dict[str, Any]) -> int:
    all_connected = all(service["status"] == "connected" for service in report.values())
    return status.HTTP_200_OK if all_connected else status.HTTP_503_SERVICE_UNAVAILABLE

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown."""
    logger.info("API service starting...")
    logger.info(f"Environment: {settings.environment}")

    app.state.db_engine = create_db_engine()
    app.state.db_session_factory = create_db_pool(app.state.db_engine)
    app.state.db_pool = app.state.db_session_factory
    app.state.session_cookie_name = settings.session_cookie_name
    app.state.student_session_cookie_name = settings.student_session_cookie_name
    app.state.admin_session_cookie_name = settings.admin_session_cookie_name

    await initialize_schema(app.state.db_engine)
    await bootstrap_admin(app.state.db_session_factory)

    if await check_postgres_connection(app.state.db_engine):
        logger.info("Postgres reachable at startup")
    else:
        logger.warning("Postgres not available at startup")

    yield

    db_engine = getattr(app.state, "db_engine", None)
    if db_engine is not None:
        await db_engine.dispose()

    logger.info("API service shutting down...")


app = FastAPI(
    title="Quiz Platform API",
    description="Quiz Platform API",
    version="0.3.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

# Middleware stack (order matters — outermost middleware is added last)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(LimitUploadSize, max_upload_size=settings.max_request_size_bytes)
app.add_middleware(RequestIdMiddleware)

# CORS — only if origins are configured
if settings.parsed_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.parsed_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

app.include_router(web.router)
app.include_router(quiz.router)
app.include_router(submission.router)
app.include_router(result.router)

Instrumentator().instrument(app).expose(app)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions (like 404, 403, 429) with custom HTML pages if requested."""
    request_id = getattr(request.state, "request_id", "unknown")

    redirect_response = _auth_redirect_response(request, exc)
    if redirect_response is not None:
        logger.warning("HTTP %s on %s [RequestID: %s]: %s", exc.status_code, request.url.path, request_id, exc.detail)
        return redirect_response

    # Log the error (but maybe not as 'exception' for simple 404s)
    if exc.status_code >= 500:
        logger.exception("HTTP %s on %s [RequestID: %s]", exc.status_code, request.url.path, request_id)
    else:
        logger.warning("HTTP %s on %s [RequestID: %s]: %s", exc.status_code, request.url.path, request_id, exc.detail)

    if "text/html" in request.headers.get("accept", ""):
        from app.routers.web import templates
        return templates.TemplateResponse(
            request, "error.html",
            {"error_id": request_id, "status_code": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code,
        )

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "request_id": request_id},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all for unhandled exceptions (500s)."""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception(
        "Unhandled exception on %s %s [RequestID: %s]: %s",
        request.method,
        request.url.path,
        request_id,
        exc,
    )

    if "text/html" in request.headers.get("accept", ""):
        from app.routers.web import templates
        return templates.TemplateResponse(
            request, "error.html",
            {"error_id": request_id, "status_code": 500},
            status_code=500,
        )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An unexpected error occurred. Please try again later.", "request_id": request_id},
    )


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "quiz-platform-lite",
        "environment": settings.environment
    }


@app.get("/health/live", include_in_schema=False)
async def health_live() -> Dict[str, Any]:
    return await health()


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    report = await build_dependency_report(app)
    status_code = dependency_http_status(report)
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if status_code == status.HTTP_200_OK else "degraded",
            "service": "quiz-platform-lite",
            "environment": settings.environment,
            "dependencies": report,
        },
    )


@app.get("/health/deep")
async def health_deep() -> JSONResponse:
    """Deep health check that verifies infra components and template compilation."""
    report = await build_dependency_report(app)

    # Check if Jinja2 templates are properly configured
    # by compiling the base template layout
    templates_ok = False
    templates_error = None
    try:
        from app.routers.web import templates
        templates.env.get_template("base.html")
        templates_ok = True
    except Exception as e:
        templates_error = str(e)

    report["templates"] = {
        "status": "connected" if templates_ok else "disconnected",
        "error": templates_error if not templates_ok else None
    }

    all_connected = all(service["status"] == "connected" for service in report.values())
    status_code = status.HTTP_200_OK if all_connected else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content=report,
    )


@app.get("/health/detailed")
async def health_detailed() -> JSONResponse:
    report = await build_dependency_report(app)
    status_code = dependency_http_status(report)
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if status_code == status.HTTP_200_OK else "degraded",
            "service": "quiz-platform-lite",
            "environment": settings.environment,
            "dependencies": report,
        },
    )


@app.get("/diagnostics/dependencies", include_in_schema=False)
async def diagnostics_dependencies() -> JSONResponse:
    return await health_detailed()


@app.get("/")
async def root() -> Dict[str, str]:
    return {
        "message": "Quiz Platform Lite API",
        "app_portal": "/app",
        "docs": "/docs",
        "health": "/health",
        "ready": "/health/ready",
        "detailed_health": "/health/detailed",
    }
