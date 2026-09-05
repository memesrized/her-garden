"""Integration fixtures use a dedicated PostgreSQL database, never production."""

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from her_garden.store import GardenStore


@pytest.fixture
async def store() -> AsyncIterator[GardenStore]:
    """Give each test an isolated schema in the explicitly supplied test database."""
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.fail("TEST_DATABASE_URL must point to a dedicated test database")
    schema = f"test_{uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    garden = GardenStore(make_conninfo(dsn, options=f"-csearch_path={schema}"))
    await garden.open()
    try:
        yield garden
    finally:
        await garden.close()
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
