"""FastAPI application: middleware, logging and startup."""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, config, db, runner, suites
from .api import router

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),            # OpenAI / Anthropic style
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),          # Google API keys
    re.compile(r"(?i)(authorization|x-api-key|x-goog-api-key)(['\"]?\s*[:=]\s*)\S+"),
]


class RedactSecrets(logging.Filter):
    """Last line of defence: scrub anything key-shaped from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for p in SECRET_PATTERNS:
            msg = p.sub(lambda m: (m.group(1) + m.group(2) + "[REDACTED]")
                        if m.lastindex and m.lastindex >= 2 else "[REDACTED]", msg)
        record.msg, record.args = msg, None
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactSecrets())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("arbiter")
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False
    # httpx logs full request lines at INFO; keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    db.init_db()
    with db.session() as conn:
        suites.load_seed(conn)
    runner.recover_interrupted()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Arbiter", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.settings.cors_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > config.settings.max_import_bytes:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-site"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        # Return field paths and messages only; never echo submitted values
        # (which could include an API key or a full prompt).
        errors = [{"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg", "")}
                  for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    app.include_router(router)
    return app


app = create_app()
