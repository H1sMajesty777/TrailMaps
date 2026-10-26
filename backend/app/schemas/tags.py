from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.schemas.common import HEX_COLOR_RE

TagName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    color: str


def _check_color(v: str | None) -> str | None:
    if v is not None and not HEX_COLOR_RE.fullmatch(v):
        raise ValueError("Color must look like #rrggbb")
    return v


class TagCreate(BaseModel):
    name: TagName
    color: str = Field(default="#2a7fff")

    @field_validator("color")
    @classmethod
    def _color(cls, v: str) -> str:
        return _check_color(v) or v


class TagUpdate(BaseModel):
    name: TagName | None = None
    color: str | None = None

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        return _check_color(v)
