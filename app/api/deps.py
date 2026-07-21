"""Shared FastAPI dependencies."""

from uuid import UUID

from fastapi import Depends, HTTPException, Request

import auth
from db import get_db


async def require_account(request: Request, db=Depends(get_db)) -> UUID:
    """Authenticate the posting API key (Authorization: Bearer or X-API-Key)."""
    api_key = auth.extract_api_key(request.headers)

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail=f"Posting requires an API key. {auth.REGISTER_HINT}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    account_id = await auth.authenticate(db, api_key)
    if account_id is None:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid or revoked API key. {auth.REGISTER_HINT}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return account_id
