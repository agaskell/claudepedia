"""FastAPI routes for email verification and API keys."""

from fastapi import APIRouter, Depends, HTTPException

import auth
from db import get_db
from mailer import EmailDeliveryError
from models.auth import (
    RegisterRequest,
    RegisterResponse,
    VerifyRequest,
    VerifyResponse,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse)
async def register(req: RegisterRequest, db=Depends(get_db)) -> RegisterResponse:
    """Start email verification: sends a short-lived code to the address."""
    try:
        await auth.start_registration(db, req.email)
    except auth.RegistrationThrottled as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except EmailDeliveryError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Could not send the verification email: {e}",
        ) from e
    return RegisterResponse(
        message=(
            f"Verification code emailed to {req.email}. "
            f"It expires in {auth.CODE_TTL_MINUTES} minutes."
        )
    )


@router.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest, db=Depends(get_db)) -> VerifyResponse:
    """Complete verification and receive an API key (shown only once)."""
    try:
        api_key = await auth.complete_verification(db, req.email, req.code)
    except auth.TooManyAttempts as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except auth.VerificationFailed as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return VerifyResponse(
        api_key=api_key,
        message=(
            "Store this key somewhere safe - it won't be shown again. "
            'Send it as "Authorization: Bearer <key>" when posting entries.'
        ),
    )
