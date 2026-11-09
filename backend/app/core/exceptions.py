"""Application exceptions and the handlers that turn them into a uniform JSON error body:
{"error": {"code": str, "message": str, "details": any, "request_id": str}}
"""
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var
from app.core.snowflake import SnowflakeConnectionError

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "app_error"

    headers: dict | None = None

    def __init__(self, message: str, details: Any = None, headers: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if headers:
            self.headers = headers


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class TooManyRequestsError(AppError):
    status_code = 429
    code = "too_many_requests"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMediaError(AppError):
    status_code = 415
    code = "unsupported_media_type"


class UnprocessableError(AppError):
    status_code = 422
    code = "unprocessable"


class LoadFailedError(AppError):
    status_code = 502
    code = "load_failed"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


def _body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details, "request_id": request_id_var.get()}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details), headers=exc.headers)

    @app.exception_handler(SnowflakeConnectionError)
    async def _snowflake(_: Request, exc: SnowflakeConnectionError):
        logger.error("Snowflake error: %s", exc)
        return JSONResponse(status_code=503, content=_body("database_unavailable", str(exc)))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [
            {"loc": [str(p) for p in e["loc"]], "message": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content=_body("validation_error", "Invalid request", details))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content=_body("http_error", str(exc.detail)))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("Unhandled error")
        return JSONResponse(status_code=500, content=_body("internal_error", "An unexpected error occurred"))
