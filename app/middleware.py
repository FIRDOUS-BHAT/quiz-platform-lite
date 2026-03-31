from starlette.responses import JSONResponse
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_413_CONTENT_TOO_LARGE
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestEntityTooLargeError(Exception):
    """Raised when a request exceeds the configured body size limit."""


class LimitUploadSize:
    def __init__(self, app: ASGIApp, max_upload_size: int) -> None:
        self.app = app
        self.max_upload_size = max_upload_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        content_length = headers.get("content-length")

        if content_length is not None:
            try:
                if int(content_length) > self.max_upload_size:
                    response = JSONResponse(
                        {"detail": "Request entity too large"},
                        status_code=HTTP_413_CONTENT_TOO_LARGE,
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                response = JSONResponse(
                    {"detail": "Invalid Content-Length header"},
                    status_code=HTTP_400_BAD_REQUEST,
                )
                await response(scope, receive, send)
                return

        received_bytes = 0

        async def capped_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_upload_size:
                    raise RequestEntityTooLargeError
            return message

        try:
            await self.app(scope, capped_receive, send)
        except RequestEntityTooLargeError:
            response = JSONResponse(
                {"detail": "Request entity too large"},
                status_code=HTTP_413_CONTENT_TOO_LARGE,
            )
            await response(scope, receive, send)
