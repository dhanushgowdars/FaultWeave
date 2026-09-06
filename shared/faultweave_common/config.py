from __future__ import annotations

import os


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


DATABASE_URL = env(
    "DATABASE_URL",
    "postgresql+asyncpg://faultweave:faultweave_dev@localhost:5432/faultweave",
)
JWT_SECRET = env("JWT_SECRET", "faultweave-local-jwt-secret")
SERVICE_TOKEN = env("SERVICE_TOKEN", "faultweave-local-service-token")
