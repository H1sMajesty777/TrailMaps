import math
import re
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, Field, StringConstraints

T = TypeVar("T")

PHONE_RE = re.compile(r"^\+?\d{7,15}$")
USERNAME_RE = re.compile(r"^[\w.\-]{3,30}$")
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    limit: int
    pages: int


def make_page(items: list, total: int, page: int, limit: int) -> dict:
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total / limit) if total else 0,
    }


def normalize_phone(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[\s\-().]", "", value)
    if cleaned == "":
        return None
    if not PHONE_RE.fullmatch(cleaned):
        raise ValueError("Invalid phone number")
    return cleaned


def validate_username(value: str) -> str:
    value = value.strip()
    if not USERNAME_RE.fullmatch(value):
        raise ValueError("Username must be 3-30 characters: letters, digits, '_', '.', '-'")
    return value


def validate_password(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes long")
    if not any(c.isalpha() for c in value) or not any(c.isdigit() for c in value):
        raise ValueError("Password must contain at least one letter and one digit")
    return value


Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
