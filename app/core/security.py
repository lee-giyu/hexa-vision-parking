"""API key authentication dependency for protected endpoints."""

import os

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

_MASTER_API_KEY = os.getenv("HEXAVISION_API_KEY")

if not _MASTER_API_KEY:
    raise RuntimeError(
        "Missing required environment variable: HEXAVISION_API_KEY. "
        "Set it before starting the application."
    )

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str = Security(_api_key_header)) -> str:
    """Reject the request with 403 unless the X-API-Key header matches the master key."""
    if api_key != _MASTER_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key.")
    return api_key
