"""Admin Lambda for database operations.

This Lambda is NOT exposed via API Gateway - it's invoked directly via AWS CLI
for administrative operations like moderation.

Usage:
    aws lambda invoke \
        --function-name claudepedia-admin \
        --payload '{"action": "query", "sql": "SELECT * FROM entries"}' \
        --cli-binary-format raw-in-base64-out \
        response.json

Actions:
    - query: Execute a SELECT query, returns rows as JSON
    - execute: Execute a mutating query (INSERT/UPDATE/DELETE), returns affected count
    - list: List all entries (shorthand for SELECT)
"""

import json
import logging
import os
from datetime import datetime
from uuid import UUID

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Reuse database config from main app
DATABASE_HOST = os.environ.get("DATABASE_HOST", "localhost")
DATABASE_PORT = os.environ.get("DATABASE_PORT", "5432")
DATABASE_NAME = os.environ.get("DATABASE_NAME", "claudepedia")
DATABASE_USER = os.environ.get("DATABASE_USER", "claudepedia_app")
USE_IAM_AUTH = os.environ.get("USE_IAM_AUTH", "false").lower() == "true"


class JSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for UUID, datetime, etc."""

    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


async def get_connection():
    """Get a database connection using IAM auth."""
    import asyncpg
    import boto3

    if USE_IAM_AUTH:
        client = boto3.client(
            "rds", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
        password = client.generate_db_auth_token(
            DBHostname=DATABASE_HOST,
            Port=int(DATABASE_PORT),
            DBUsername=DATABASE_USER,
        )
    else:
        password = os.environ.get("DATABASE_PASSWORD", "")

    conn = await asyncpg.connect(
        host=DATABASE_HOST,
        port=int(DATABASE_PORT),
        database=DATABASE_NAME,
        user=DATABASE_USER,
        password=password,
        ssl="require" if USE_IAM_AUTH else None,
    )
    return conn


async def handle_query(sql: str) -> dict:
    """Execute a SELECT query and return results."""
    conn = await get_connection()
    try:
        rows = await conn.fetch(sql)
        # Convert to list of dicts
        results = [dict(row) for row in rows]
        return {
            "success": True,
            "count": len(results),
            "rows": results,
        }
    finally:
        await conn.close()


async def handle_execute(sql: str) -> dict:
    """Execute a mutating query (INSERT/UPDATE/DELETE)."""
    conn = await get_connection()
    try:
        result = await conn.execute(sql)
        # Result is like "DELETE 3" or "UPDATE 1"
        return {
            "success": True,
            "result": result,
        }
    finally:
        await conn.close()


async def handle_list() -> dict:
    """List all entries (convenience method)."""
    return await handle_query(
        "SELECT id, title, LEFT(content, 100) as content_preview, tags, created_at "
        "FROM entries ORDER BY created_at DESC"
    )


async def async_handler(event: dict) -> dict:
    """Async handler for admin operations."""
    action = event.get("action", "query")
    sql = event.get("sql", "")

    logger.info(f"Admin action: {action}")

    if action == "query":
        if not sql:
            return {"success": False, "error": "Missing 'sql' parameter"}
        # Safety check: only allow SELECT
        if not sql.strip().upper().startswith("SELECT"):
            return {
                "success": False,
                "error": "Query action only allows SELECT statements",
            }
        return await handle_query(sql)

    elif action == "execute":
        if not sql:
            return {"success": False, "error": "Missing 'sql' parameter"}
        # Log the query for audit trail
        logger.warning(f"Executing mutating query: {sql}")
        return await handle_execute(sql)

    elif action == "list":
        return await handle_list()

    else:
        return {"success": False, "error": f"Unknown action: {action}"}


def handler(event, context):
    """Lambda entry point."""
    import asyncio

    try:
        result = asyncio.run(async_handler(event))
        # Use custom encoder for UUID/datetime
        return json.loads(json.dumps(result, cls=JSONEncoder))
    except Exception as e:
        logger.exception("Admin handler error")
        return {
            "success": False,
            "error": str(e),
        }
