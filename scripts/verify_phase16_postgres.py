"""Run the multi-service integration suite using isolated schemas on local PostgreSQL."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    url = os.getenv("FW_TRANSFER_TEST_DATABASE_URL")
    if not url:
        url = URL.create(
            "postgresql+asyncpg",
            username=os.getenv("POSTGRES_USER", "faultweave"),
            password=os.getenv("POSTGRES_PASSWORD", "faultweave_dev"),
            host="localhost",
            port=int(os.getenv("POSTGRES_HOST_PORT", "15434")),
            database=os.getenv("POSTGRES_DB", "faultweave"),
        ).render_as_string(hide_password=False)
    env = {**os.environ, "FW_TRANSFER_TEST_DATABASE_URL": url}
    print("Running real PostgreSQL integration checks in isolated fwtest_* schemas.", flush=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_transfer_integration.py",
            "-o",
            "addopts=",
            "-q",
        ],
        cwd=root,
        env=env,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
