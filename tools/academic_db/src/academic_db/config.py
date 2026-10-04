"""Settings from the environment, falling back to the repository-root .env file."""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path


@cache
def _dotenv() -> dict[str, str]:
    """Parse the nearest .env above this package (the repository root)."""
    for directory in Path(__file__).resolve().parents:
        path = directory / ".env"
        if path.is_file():
            values = {}
            for line in path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip("'\"")
            return values
    return {}


def setting(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or _dotenv().get(name) or default


def writer_url() -> str:
    url = setting("ACADEMIC_DB_URL")
    if not url:
        raise RuntimeError("ACADEMIC_DB_URL is not set (environment or repository-root .env)")
    return url


def reader_url() -> str:
    url = setting("ACADEMIC_DB_READER_URL")
    if not url:
        raise RuntimeError("ACADEMIC_DB_READER_URL is not set (environment or repository-root .env)")
    return url


def agent_name() -> str:
    """Who is calling, recorded in created_by / assessed_by. Set per agent in its YAML."""
    return setting("ACADEMIC_DB_AGENT", "unknown")
