"""Connection pools: a read-only login for reads, the owner login for writes."""

from __future__ import annotations

import atexit
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

_pools: dict[str, ConnectionPool] = {}


def _pool(kind: str) -> ConnectionPool:
    if kind not in _pools:
        url = config.reader_url() if kind == "reader" else config.writer_url()
        pool = ConnectionPool(
            url,
            min_size=0,
            max_size=2,
            kwargs={"row_factory": dict_row, "application_name": f"academic-db-mcp ({config.agent_name()})"},
            check=ConnectionPool.check_connection,
            open=True,
        )
        atexit.register(pool.close)
        _pools[kind] = pool
    return _pools[kind]


@contextmanager
def reader() -> Iterator[psycopg.Connection]:
    """A read-only connection. Every block runs in a transaction that is rolled back."""
    with _pool("reader").connection() as conn:
        try:
            yield conn
        finally:
            conn.rollback()


@contextmanager
def writer() -> Iterator[psycopg.Connection]:
    """A read-write connection. The block commits on success and rolls back on error."""
    with _pool("writer").connection() as conn:
        yield conn


def jsonable(value: Any) -> Any:
    """Convert database values (Decimal, datetime, nested rows) into JSON-friendly ones."""
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value
