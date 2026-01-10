"""Database setup supporting SQLite (local) and Postgres (Lambda)."""

import json
import logging
import os
from pathlib import Path
from typing import AsyncGenerator, Protocol

import aiosqlite

logger = logging.getLogger(__name__)

# Environment-based config
USE_POSTGRES = os.environ.get("DATABASE_HOST") is not None
DATABASE_HOST = os.environ.get("DATABASE_HOST", "localhost")
DATABASE_PORT = os.environ.get("DATABASE_PORT", "5432")
DATABASE_NAME = os.environ.get("DATABASE_NAME", "claudepedia")
DATABASE_USER = os.environ.get("DATABASE_USER", "claudepedia_app")
USE_IAM_AUTH = os.environ.get("USE_IAM_AUTH", "false").lower() == "true"
ADMIN_SECRET_ARN = os.environ.get("ADMIN_SECRET_ARN", "")

# SQLite path for local dev (project root, not app directory)
SQLITE_PATH = Path(__file__).parent.parent.parent / "claudepedia.db"

# Track if we've bootstrapped
_bootstrapped = False


class DbConnection(Protocol):
    """Protocol for database connections (SQLite or Postgres)."""

    async def execute(self, query: str, params: tuple = ()) -> any: ...
    async def fetchone(self) -> any: ...
    async def fetchall(self) -> list: ...
    async def commit(self) -> None: ...


async def get_iam_auth_token() -> str:
    """Generate IAM auth token for RDS connection."""
    import boto3

    client = boto3.client("rds", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    token = client.generate_db_auth_token(
        DBHostname=DATABASE_HOST,
        Port=int(DATABASE_PORT),
        DBUsername=DATABASE_USER,
    )
    return token


def get_admin_credentials() -> tuple[str, str]:
    """Get admin username/password from Secrets Manager."""
    import boto3

    if not ADMIN_SECRET_ARN:
        raise ValueError("ADMIN_SECRET_ARN not set")

    client = boto3.client(
        "secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1")
    )
    response = client.get_secret_value(SecretId=ADMIN_SECRET_ARN)
    secret = json.loads(response["SecretString"])
    return secret["username"], secret["password"]


async def _bootstrap_iam_user() -> None:
    """Create the IAM-authenticated database user using admin credentials."""
    import asyncpg

    global _bootstrapped
    if _bootstrapped:
        return

    logger.info(f"Bootstrapping IAM user '{DATABASE_USER}'...")

    logger.info("Fetching admin credentials from Secrets Manager...")
    username, password = get_admin_credentials()
    logger.info(f"Got admin credentials, username={username}")

    logger.info(f"Connecting to database as admin at {DATABASE_HOST}...")
    conn = await asyncpg.connect(
        host=DATABASE_HOST,
        port=int(DATABASE_PORT),
        database=DATABASE_NAME,
        user=username,
        password=password,
        ssl="require",
    )
    logger.info("Admin connection established")

    try:
        # Check if user exists
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname = $1",
            DATABASE_USER,
        )

        if not exists:
            logger.info(f"Creating user '{DATABASE_USER}'...")
            await conn.execute(f'CREATE USER "{DATABASE_USER}"')
            await conn.execute(f'GRANT rds_iam TO "{DATABASE_USER}"')
            await conn.execute(
                f'GRANT ALL PRIVILEGES ON DATABASE "{DATABASE_NAME}" TO "{DATABASE_USER}"'
            )
            # Grant schema permissions
            await conn.execute(f'GRANT ALL ON SCHEMA public TO "{DATABASE_USER}"')
            await conn.execute(
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "{DATABASE_USER}"'
            )
            await conn.execute(
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO "{DATABASE_USER}"'
            )
            logger.info(f"User '{DATABASE_USER}' created with IAM auth enabled")
        else:
            logger.info(f"User '{DATABASE_USER}' already exists")

        _bootstrapped = True
    finally:
        await conn.close()


async def _get_pg_connection():
    """Get a new Postgres connection (no pooling for Lambda compatibility).

    Schema is managed by yoyo migrations (see db/migrate.py).
    """
    import asyncpg

    logger.info(
        f"Creating Postgres connection: host={DATABASE_HOST}, iam={USE_IAM_AUTH}"
    )

    # Bootstrap IAM user if needed (only on first call)
    if USE_IAM_AUTH and ADMIN_SECRET_ARN and not _bootstrapped:
        try:
            logger.info("Starting IAM user bootstrap...")
            await _bootstrap_iam_user()
            logger.info("IAM user bootstrap complete")
        except Exception as e:
            logger.warning(f"Bootstrap failed (may already be done): {e}")

    password = (
        await get_iam_auth_token()
        if USE_IAM_AUTH
        else os.environ.get("DATABASE_PASSWORD", "")
    )
    conn = await asyncpg.connect(
        host=DATABASE_HOST,
        port=int(DATABASE_PORT),
        database=DATABASE_NAME,
        user=DATABASE_USER,
        password=password,
        ssl="require" if USE_IAM_AUTH else None,
    )

    return conn


class PostgresConnection:
    """Wrapper for asyncpg connection to match our interface."""

    def __init__(self, conn):
        self._conn = conn
        self._cursor = None

    async def execute(self, query: str, params: tuple = ()):
        """Execute a query."""
        # Convert ? placeholders to $1, $2, etc for asyncpg
        pg_query = query
        for i in range(len(params)):
            pg_query = pg_query.replace("?", f"${i + 1}", 1)
        self._cursor = await self._conn.fetch(pg_query, *params)
        return self

    async def fetchone(self):
        """Fetch one row."""
        if self._cursor and len(self._cursor) > 0:
            return self._cursor[0]
        return None

    async def fetchall(self):
        """Fetch all rows."""
        return self._cursor or []

    async def commit(self):
        """No-op for asyncpg (autocommit)."""
        pass


async def get_db() -> AsyncGenerator:
    """Dependency for getting database connection."""
    if USE_POSTGRES:
        conn = await _get_pg_connection()
        try:
            yield PostgresConnection(conn)
        finally:
            await conn.close()
    else:
        async with aiosqlite.connect(SQLITE_PATH) as db:
            db.row_factory = aiosqlite.Row
            yield db
