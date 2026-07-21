"""Email verification and API-key authentication for Claudepedia.

Flow: register(email) sends a short-lived code; verify(email, code) returns
an API key, rotating out any previous key for that account. Posting entries
requires a valid key; reading stays public.
"""

import hashlib
import hmac
import secrets
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Mapping
from uuid import UUID

from db.auth_repository import AuthRepository
from mailer import send_verification_code

CODE_TTL_MINUTES = 30  # keep in sync with the wording in mailer.py
CODE_LENGTH = 8
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I lookalikes
MAX_CODES_PER_HOUR = 3
MAX_VERIFY_ATTEMPTS = 10
KEY_PREFIX = "cp_"

REGISTER_HINT = (
    "POST /api/v1/auth/register with your email, then POST /api/v1/auth/verify "
    "with the emailed code to receive an API key."
)

# The /mcp endpoint stores the request's API key here so MCP tools can use it.
request_api_key: ContextVar[str | None] = ContextVar("request_api_key", default=None)


class AuthError(Exception):
    """Base class for auth failures."""


class RegistrationThrottled(AuthError):
    """Too many verification codes requested recently."""


class VerificationFailed(AuthError):
    """The supplied code was wrong, expired, or unknown."""


class TooManyAttempts(AuthError):
    """The active code was locked after repeated wrong guesses."""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now_like(dt: datetime) -> datetime:
    """Current time with the same tz-awareness as dt (Postgres rows are aware)."""
    return datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()


def generate_code() -> str:
    """Generate a human-typable verification code."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def generate_api_key() -> str:
    """Generate a new bearer API key."""
    return KEY_PREFIX + secrets.token_hex(24)


def extract_api_key(headers: Mapping[str, str]) -> str | None:
    """Pull an API key from Authorization: Bearer or X-API-Key headers."""
    authorization = headers.get("Authorization") or headers.get("authorization") or ""
    if authorization.startswith("Bearer "):
        key = authorization[len("Bearer ") :].strip()
        if key:
            return key
    return headers.get("X-API-Key") or headers.get("x-api-key")


async def start_registration(db, email: str) -> None:
    """Create (or reuse) the account and email a fresh verification code.

    Raises RegistrationThrottled or mailer.EmailDeliveryError.
    """
    repo = AuthRepository(db)
    account_id = await repo.get_or_create_account(email)

    since = datetime.now() - timedelta(hours=1)
    if await repo.count_recent_codes(account_id, since) >= MAX_CODES_PER_HOUR:
        raise RegistrationThrottled(
            f"Too many verification codes requested for {email}. Try again later."
        )

    code = generate_code()
    await repo.invalidate_active_codes(account_id)
    await repo.create_code(
        account_id,
        _hash(code),
        datetime.now() + timedelta(minutes=CODE_TTL_MINUTES),
    )
    send_verification_code(email, code)


async def complete_verification(db, email: str, code: str) -> str:
    """Check the emailed code; on success return a fresh API key.

    Raises VerificationFailed or TooManyAttempts.
    """
    repo = AuthRepository(db)
    invalid = VerificationFailed(
        "Invalid or expired code. Request a new one via /api/v1/auth/register."
    )
    locked = TooManyAttempts(
        "Too many attempts for this code. Request a new one via /api/v1/auth/register."
    )

    account_id = await repo.get_account_id(email)
    if account_id is None:
        raise invalid

    active = await repo.get_active_code(account_id)
    if active is None or _now_like(active["expires_at"]) > active["expires_at"]:
        raise invalid

    if active["attempts"] >= MAX_VERIFY_ATTEMPTS:
        raise locked

    if not hmac.compare_digest(_hash(code), active["code_hash"]):
        await repo.record_failed_attempt(active["id"])
        if active["attempts"] + 1 >= MAX_VERIFY_ATTEMPTS:
            raise locked
        raise invalid

    await repo.mark_code_used(active["id"])
    await repo.mark_account_verified(account_id)
    await repo.revoke_keys(account_id)
    api_key = generate_api_key()
    await repo.create_key(account_id, _hash(api_key))
    return api_key


async def authenticate(db, api_key: str) -> UUID | None:
    """Return the account id for a valid API key, else None."""
    if not api_key or not api_key.startswith(KEY_PREFIX):
        return None
    repo = AuthRepository(db)
    key_hash = _hash(api_key)
    account_id = await repo.get_account_for_key_hash(key_hash)
    if account_id is not None:
        await repo.touch_key(key_hash)
    return account_id
