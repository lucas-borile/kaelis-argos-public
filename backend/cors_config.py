from __future__ import annotations

import json
import os
from urllib.parse import urlparse


DEFAULT_DEVELOPMENT_ORIGINS = [
    "http://127.0.0.1:8080",
    "http://localhost:8080",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
]


def is_production_environment() -> bool:
    value = os.getenv("ARGOS_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
    return value in {"prod", "production"}


def parse_cors_origins(raw_value: str | None = None, *, production: bool | None = None) -> list[str]:
    raw = os.getenv("ARGOS_CORS_ORIGINS", "") if raw_value is None else raw_value
    is_production = is_production_environment() if production is None else production
    origins = _split_origins(raw)
    if not origins:
        return [] if is_production else DEFAULT_DEVELOPMENT_ORIGINS.copy()
    normalized = []
    for origin in origins:
        value = origin.rstrip("/")
        if value == "*":
            if is_production:
                raise RuntimeError("ARGOS_CORS_ORIGINS cannot use '*' in production")
            normalized.append(value)
            continue
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError(f"invalid CORS origin: {origin!r}")
        normalized.append(value)
    return sorted(set(normalized))


def _split_origins(raw: str) -> list[str]:
    value = str(raw or "").strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError("ARGOS_CORS_ORIGINS JSON is invalid") from exc
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise RuntimeError("ARGOS_CORS_ORIGINS JSON must be a list of strings")
        return [item.strip() for item in parsed if item.strip()]
    return [item.strip() for item in value.split(",") if item.strip()]
