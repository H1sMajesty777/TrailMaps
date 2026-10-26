from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import normalize_phone, validate_username


class UserMe(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    phone: str | None
    username: str
    avatar_url: str
    bio: str
    role: str
    is_active: bool
    created_at: datetime
    last_login: datetime | None


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    avatar_url: str
    bio: str
    created_at: datetime


class UserUpdate(BaseModel):
    username: str | None = None
    bio: str | None = Field(default=None, max_length=2000)
    phone: str | None = None

    @field_validator("username")
    @classmethod
    def _username(cls, v: str | None) -> str | None:
        return None if v is None else validate_username(v)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)

    @model_validator(mode="after")
    def _not_empty(self) -> "UserUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        if "username" in self.model_fields_set and self.username is None:
            raise ValueError("username cannot be null")
        return self
