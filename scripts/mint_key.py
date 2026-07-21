"""Mint a Claudepedia API key directly via the admin Lambda.

Bypasses email verification - the admin equivalent of the register/verify
flow, useful while the AWS account's SES is sandboxed and can't deliver to
arbitrary addresses. Creates (or reuses) the account for the given email,
marks it verified, and adds a new API key without revoking existing ones.

Usage: python3 mint_key.py someone@example.com
"""

import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone

ADMIN_LAMBDA = os.environ.get("CLAUDEPEDIA_ADMIN_LAMBDA", "claudepedia-admin-dev")


def invoke(action: str, sql: str) -> dict:
    """Invoke the admin Lambda with a payload file (avoids shell quoting)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"action": action, "sql": sql}, f)
        payload_path = f.name
    response_path = payload_path + ".out"

    subprocess.run(
        [
            "uvx",
            "--from",
            "awscli",
            "aws",
            "lambda",
            "invoke",
            "--function-name",
            ADMIN_LAMBDA,
            "--payload",
            f"file://{payload_path}",
            response_path,
        ],
        check=True,
        capture_output=True,
    )
    with open(response_path) as f:
        result = json.load(f)
    if not result.get("success"):
        raise SystemExit(f"Admin Lambda error: {result.get('error')}")
    return result


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: mint_key.py <email>")
    email = sys.argv[1].strip().lower().replace("'", "''")

    now = datetime.now(timezone.utc).isoformat()
    result = invoke("query", f"SELECT id FROM accounts WHERE email = '{email}'")
    if result["rows"]:
        account_id = result["rows"][0]["id"]
        invoke(
            "execute",
            f"UPDATE accounts SET verified_at = COALESCE(verified_at, '{now}') "
            f"WHERE id = '{account_id}'",
        )
    else:
        account_id = str(uuid.uuid4())
        invoke(
            "execute",
            "INSERT INTO accounts (id, email, created_at, verified_at) "
            f"VALUES ('{account_id}', '{email}', '{now}', '{now}')",
        )

    api_key = "cp_" + secrets.token_hex(24)
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    invoke(
        "execute",
        "INSERT INTO api_keys (id, account_id, key_hash, created_at) "
        f"VALUES ('{uuid.uuid4()}', '{account_id}', '{key_hash}', '{now}')",
    )

    print(f"API key for {email} (store it - shown only once):")
    print(f"  {api_key}")


if __name__ == "__main__":
    main()
