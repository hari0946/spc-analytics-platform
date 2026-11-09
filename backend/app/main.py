import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.api.routes import health
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import request_id_var, setup_logging
from app.core.snowflake import close_connection

settings = get_settings()
setup_logging(settings.log_level)
logger = logging.getLogger("app.request")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)
    yield
    close_connection()
    logger.info("Shutdown complete")


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    start = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:  # unhandled: log with request id, return the uniform error body
            logger.exception("Unhandled error")
            response = JSONResponse(
                status_code=500,
                content={"error": {"code": "internal_error", "message": "An unexpected error occurred",
                                   "details": None, "request_id": rid}},
            )
        ms = (time.perf_counter() - start) * 1000
        logger.info("%s %s -> %s (%.0f ms)", request.method, request.url.path, response.status_code, ms)
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        request_id_var.reset(token)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    h = response.headers
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "no-referrer"
    h["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if request.url.path.startswith("/api"):
        h["Cache-Control"] = "no-store"
        h["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


register_exception_handlers(app)

# Unprefixed /health for container/infra probes; everything else under /api
app.include_router(health.router)
app.include_router(api_router, prefix="/api")
