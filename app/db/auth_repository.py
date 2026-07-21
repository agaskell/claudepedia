"""Auth repository for accounts, verification codes, and API keys."""

from datetime import datetime
from uuid import UUID, uuid4

from db.repository import from_datetime, from_uuid, needs_commit, to_param


class AuthRepository:
    """Repository for authentication-related database operations."""

    def __init__(self, db):
        self.db = db

    async def _commit_if_needed(self):
        """Commit transaction if database requires it."""
        if needs_commit():
            await self.db.commit()

    async def get_or_create_account(self, email: str) -> UUID:
        """Return the account id for an email, creating the account if new."""
        account_id = await self.get_account_id(email)
        if account_id is not None:
            return account_id

        account_id = uuid4()
        await self.db.execute(
            "INSERT INTO accounts (id, email, created_at) VALUES (?, ?, ?)",
            (to_param(account_id), email, to_param(datetime.now())),
        )
        await self._commit_if_needed()
        return account_id

    async def get_account_id(self, email: str) -> UUID | None:
        """Look up an account id by email."""
        cursor = await self.db.execute(
            "SELECT id FROM accounts WHERE email = ?", (email,)
        )
        row = await cursor.fetchone()
        return from_uuid(row["id"]) if row else None

    async def count_recent_codes(self, account_id: UUID, since: datetime) -> int:
        """Count verification codes created for an account since a timestamp."""
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM verification_codes "
            "WHERE account_id = ? AND created_at > ?",
            (to_param(account_id), to_param(since)),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def invalidate_active_codes(self, account_id: UUID) -> None:
        """Mark all unused codes for an account as used (newest code wins)."""
        await self.db.execute(
            "UPDATE verification_codes SET used_at = ? "
            "WHERE account_id = ? AND used_at IS NULL",
            (to_param(datetime.now()), to_param(account_id)),
        )
        await self._commit_if_needed()

    async def create_code(
        self, account_id: UUID, code_hash: str, expires_at: datetime
    ) -> None:
        """Store a new (hashed) verification code."""
        await self.db.execute(
            """
            INSERT INTO verification_codes
                (id, account_id, code_hash, created_at, expires_at, attempts)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (
                to_param(uuid4()),
                to_param(account_id),
                code_hash,
                to_param(datetime.now()),
                to_param(expires_at),
            ),
        )
        await self._commit_if_needed()

    async def get_active_code(self, account_id: UUID) -> dict | None:
        """Get the newest unused code for an account, if any."""
        cursor = await self.db.execute(
            """
            SELECT id, code_hash, expires_at, attempts FROM verification_codes
            WHERE account_id = ? AND used_at IS NULL
            ORDER BY created_at DESC LIMIT 1
            """,
            (to_param(account_id),),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": from_uuid(row["id"]),
            "code_hash": row["code_hash"],
            "expires_at": from_datetime(row["expires_at"]),
            "attempts": row["attempts"],
        }

    async def record_failed_attempt(self, code_id: UUID) -> None:
        """Increment the attempt counter for a code."""
        await self.db.execute(
            "UPDATE verification_codes SET attempts = attempts + 1 WHERE id = ?",
            (to_param(code_id),),
        )
        await self._commit_if_needed()

    async def mark_code_used(self, code_id: UUID) -> None:
        """Consume a verification code."""
        await self.db.execute(
            "UPDATE verification_codes SET used_at = ? WHERE id = ?",
            (to_param(datetime.now()), to_param(code_id)),
        )
        await self._commit_if_needed()

    async def mark_account_verified(self, account_id: UUID) -> None:
        """Record the first successful verification for an account."""
        await self.db.execute(
            "UPDATE accounts SET verified_at = ? WHERE id = ? AND verified_at IS NULL",
            (to_param(datetime.now()), to_param(account_id)),
        )
        await self._commit_if_needed()

    async def revoke_keys(self, account_id: UUID) -> None:
        """Revoke all active API keys for an account (rotation)."""
        await self.db.execute(
            "UPDATE api_keys SET revoked_at = ? "
            "WHERE account_id = ? AND revoked_at IS NULL",
            (to_param(datetime.now()), to_param(account_id)),
        )
        await self._commit_if_needed()

    async def create_key(self, account_id: UUID, key_hash: str) -> None:
        """Store a new (hashed) API key."""
        await self.db.execute(
            "INSERT INTO api_keys (id, account_id, key_hash, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                to_param(uuid4()),
                to_param(account_id),
                key_hash,
                to_param(datetime.now()),
            ),
        )
        await self._commit_if_needed()

    async def get_account_for_key_hash(self, key_hash: str) -> UUID | None:
        """Return the account for an active key on a verified account."""
        cursor = await self.db.execute(
            """
            SELECT a.id FROM api_keys k
            JOIN accounts a ON a.id = k.account_id
            WHERE k.key_hash = ? AND k.revoked_at IS NULL
              AND a.verified_at IS NOT NULL
            """,
            (key_hash,),
        )
        row = await cursor.fetchone()
        return from_uuid(row["id"]) if row else None

    async def touch_key(self, key_hash: str) -> None:
        """Update last_used_at for a key."""
        await self.db.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE key_hash = ?",
            (to_param(datetime.now()), key_hash),
        )
        await self._commit_if_needed()
