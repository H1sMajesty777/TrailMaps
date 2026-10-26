from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import Latitude, Longitude, ShortText
from app.schemas.tags import TagOut

MAX_WAYPOINTS = 25
MAX_TAGS_PER_ROUTE = 10


class RouteSort(StrEnum):
    POPULAR = "popular"
    LIKES = "likes"
    VIEWS = "views"
    NEW = "new"
    DURATION = "duration"
    DISTANCE = "distance"


class WaypointIn(BaseModel):
    lat: Latitude
    lng: Longitude


class GeoJSONLineString(BaseModel):
    type: Literal["LineString"] = "LineString"
    coordinates: list[list[float]]


class BuildRouteRequest(BaseModel):
    waypoints: list[WaypointIn] = Field(min_length=2, max_length=MAX_WAYPOINTS)


class BuildRouteResponse(BaseModel):
    geometry: GeoJSONLineString
    duration: int
    distance: int


class RouteCreate(BaseModel):
    name: ShortText
    description: str = Field(default="", max_length=5000)
    waypoints: list[WaypointIn] = Field(min_length=2, max_length=MAX_WAYPOINTS)
    tag_ids: list[int] = Field(default_factory=list, max_length=MAX_TAGS_PER_ROUTE)
    is_public: bool = True


class RouteUpdate(BaseModel):
    name: ShortText | None = None
    description: str | None = Field(default=None, max_length=5000)
    waypoints: list[WaypointIn] | None = Field(default=None, min_length=2, max_length=MAX_WAYPOINTS)
    tag_ids: list[int] | None = Field(default=None, max_length=MAX_TAGS_PER_ROUTE)
    is_public: bool | None = None

    @field_validator("name", "description", "waypoints", "tag_ids", "is_public")
    @classmethod
    def _not_null(cls, v):  # explicit null is not a valid way to "unset" these fields
        if v is None:
            raise ValueError("Field cannot be null")
        return v

    @model_validator(mode="after")
    def _not_empty(self) -> "RouteUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        return self


class AuthorOut(BaseModel):
    id: int
    username: str
    avatar_url: str


class RouteOut(BaseModel):
    id: int
    name: str
    description: str
    author: AuthorOut
    duration: int
    distance: int
    is_public: bool
    is_hidden: bool
    tags: list[TagOut]
    views_count: int
    likes_count: int
    geometry: GeoJSONLineString
    created_at: datetime
    updated_at: datetime
    is_liked: bool = False
    distance_from_user: int | None = None


class WaypointOut(BaseModel):
    lat: float
    lng: float


class RouteDetail(RouteOut):
    waypoints: list[WaypointOut]


class LikeState(BaseModel):
    liked: bool
    likes_count: int


class VisibilityUpdate(BaseModel):
    is_hidden: bool


class VisibilityOut(BaseModel):
    id: int
    is_hidden: bool
