"""Auth models for claudepedia."""

import re

from pydantic import BaseModel, Field, field_validator

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    """Request model for starting email verification."""

    email: str = Field(..., max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_PATTERN.fullmatch(v):
            raise ValueError("must be a valid email address")
        return v


class RegisterResponse(BaseModel):
    """Response model for a registration request."""

    message: str


class VerifyRequest(BaseModel):
    """Request model for completing email verification."""

    email: str = Field(..., max_length=320)
    code: str = Field(..., min_length=1, max_length=32)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("code")
    @classmethod
    def normalize_code(cls, v: str) -> str:
        return v.strip().upper()


class VerifyResponse(BaseModel):
    """Response model carrying the freshly issued API key."""

    api_key: str
    message: str
