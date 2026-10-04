import dataclasses
import json
import random
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from academic_db import config
from academic_db.normalize import Record

FIXTURES = Path(__file__).parent / "fixtures"

# The database is live and holds real papers, so test records get IDs and titles unique to this run.
RUN = uuid.uuid4().hex[:8]
_MAG_OFFSET = random.randint(1, 1_000_000) * 10**12


def isolated(rec: Record) -> Record:
    """Rewrite a fixture record's IDs and title consistently so it can only match other test records."""
    return dataclasses.replace(
        rec,
        provider_work_id=f"{rec.provider_work_id}~{RUN}",
        title=f"{rec.title} {RUN}",
        doi=f"10.99999/{RUN}/{rec.doi}" if rec.doi else None,
        arxiv_id=f"{rec.arxiv_id}~{RUN}" if rec.arxiv_id else None,
        mag_id=rec.mag_id + _MAG_OFFSET if rec.mag_id else None,
    )


def load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def conn():
    """Writer connection inside an outer transaction that is always rolled back.

    The code under test opens its own conn.transaction() blocks; nested inside this one they
    become savepoints, so nothing is ever committed.
    """
    try:
        url = config.writer_url()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with psycopg.connect(url, row_factory=dict_row) as connection:
        with connection.transaction(force_rollback=True):
            yield connection
