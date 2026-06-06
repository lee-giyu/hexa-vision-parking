"""FastAPI application entry point with CORS, router, and error handling."""

import logging
import os
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.endpoints import parking as parking_router

logger = logging.getLogger("hexa-vision")

# In production the backend is reachable over a public tunnel, so the
# interactive docs are disabled to avoid handing the full API surface to
# anonymous callers. Set APP_ENV=development to re-enable Swagger/ReDoc locally.
_docs_enabled = os.getenv("APP_ENV", "development").strip().lower() != "production"

app = FastAPI(
    title="Hexa-Vision Parking API",
    description="Smart parking management system with real-time DB integration.",
    version="1.0.0",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# ---------------------------------------------------------------------------
# CORS — restrict to known Frontend origins via environment variable.
# Default allows common Tailscale dev addresses; override in .env for prod.
# To allow all origins during early development, set ALLOWED_ORIGINS=* in .env.
# ---------------------------------------------------------------------------
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

_app_env = os.getenv("APP_ENV", "development").strip().lower()
if _app_env == "production" and "*" in ALLOWED_ORIGINS:
    raise RuntimeError(
        "Wildcard CORS origin ('*') is not permitted in production. "
        "Set ALLOWED_ORIGINS to explicit frontend domains in .env."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(parking_router.router)


# ---------------------------------------------------------------------------
# Global exception handler — hide internals from the client, log full trace.
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Intercept unhandled exceptions and return a safe 500 response.

    Logs the full stack trace to the server console for debugging while
    returning only a generic error message to the client.

    Args:
        request: The incoming HTTP request that triggered the error.
        exc: The unhandled exception instance.

    Returns:
        JSONResponse: A 500 response with a generic error message.
    """
    logger.error(
        "Unhandled %s on %s %s:\n%s",
        type(exc).__name__,
        request.method,
        request.url,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )
