from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import normalize_phone, validate_password, validate_username


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str
    password: str
    phone: str | None = None

    @field_validator("email")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return v.lower()

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        return validate_username(v)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        return validate_password(v)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return v.lower()


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=4096)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_expires_in: int
