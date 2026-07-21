"""Database migration runner using yoyo-migrations.

Supports both SQLite (local) and Postgres (production) with automatic
database type detection based on environment variables.
"""

import logging
import os
from pathlib import Path

from yoyo import get_backend, read_migrations

logger = logging.getLogger(__name__)

# Environment-based config (same as database.py)
USE_POSTGRES = os.environ.get("DATABASE_HOST") is not None
DATABASE_HOST = os.environ.get("DATABASE_HOST", "localhost")
DATABASE_PORT = os.environ.get("DATABASE_PORT", "5432")
DATABASE_NAME = os.environ.get("DATABASE_NAME", "claudepedia")
DATABASE_USER = os.environ.get("DATABASE_USER", "claudepedia_app")
USE_IAM_AUTH = os.environ.get("USE_IAM_AUTH", "false").lower() == "true"

# SQLite path for local dev (project root, not app directory);
# CLAUDEPEDIA_DB overrides it (used by the test suite)
SQLITE_PATH = Path(
    os.environ.get("CLAUDEPEDIA_DB")
    or Path(__file__).parent.parent.parent / "claudepedia.db"
)

# Migrations directory
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def is_postgres() -> bool:
    """Check if we're using Postgres (production) or SQLite (local)."""
    return USE_POSTGRES


def get_iam_auth_token() -> str:
    """Generate IAM auth token for RDS connection."""
    import boto3

    client = boto3.client("rds", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    token = client.generate_db_auth_token(
        DBHostname=DATABASE_HOST,
        Port=int(DATABASE_PORT),
        DBUsername=DATABASE_USER,
    )
    return token


def get_connection_url() -> str:
    """Return database URL for yoyo migrations.

    SQLite: sqlite:///path/to/db.sqlite
    Postgres: postgresql://user:password@host:port/dbname
    """
    if USE_POSTGRES:
        if USE_IAM_AUTH:
            password = get_iam_auth_token()
        else:
            password = os.environ.get("DATABASE_PASSWORD", "")

        # URL-encode password in case it contains special chars
        from urllib.parse import quote_plus

        encoded_password = quote_plus(password)

        # yoyo needs sslmode for Postgres with IAM
        sslmode = "require" if USE_IAM_AUTH else "prefer"

        return (
            f"postgresql://{DATABASE_USER}:{encoded_password}"
            f"@{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_NAME}"
            f"?sslmode={sslmode}"
        )
    else:
        # SQLite - yoyo expects sqlite:/// prefix
        return f"sqlite:///{SQLITE_PATH}"


def run_migrations() -> None:
    """Apply all pending migrations.

    Call this at application startup to ensure schema is up to date.
    Uses yoyo's locking mechanism to prevent concurrent migrations.
    """
    logger.info(f"Running migrations from {MIGRATIONS_DIR}")
    logger.info(f"Database: {'Postgres' if USE_POSTGRES else 'SQLite'}")

    url = get_connection_url()
    # Don't log the full URL (contains password)
    logger.debug(f"Connection URL: {url[:50]}...")

    backend = get_backend(url)
    migrations = read_migrations(str(MIGRATIONS_DIR))

    pending = backend.to_apply(migrations)
    if not pending:
        logger.info("No pending migrations")
        return

    logger.info(f"Applying {len(pending)} migration(s): {[m.id for m in pending]}")

    with backend.lock():
        backend.apply_migrations(pending)

    logger.info("Migrations complete")


def rollback_migrations(count: int = 1) -> None:
    """Rollback the last N migrations.

    Args:
        count: Number of migrations to roll back (default: 1)
    """
    logger.info(f"Rolling back {count} migration(s)")

    url = get_connection_url()
    backend = get_backend(url)
    migrations = read_migrations(str(MIGRATIONS_DIR))

    # Get applied migrations in reverse order
    to_rollback = list(backend.to_rollback(migrations))[:count]

    if not to_rollback:
        logger.info("No migrations to roll back")
        return

    logger.info(f"Rolling back: {[m.id for m in to_rollback]}")

    with backend.lock():
        backend.rollback_migrations(to_rollback)

    logger.info("Rollback complete")


if __name__ == "__main__":
    # Allow running migrations from command line: python -m db.migrate
    logging.basicConfig(level=logging.INFO)
    run_migrations()
