from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, model_validator

from app.db.models import Role
from app.schemas.users import UserMe


class AdminUserUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _check(self) -> "AdminUserUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        if self.role is None and "role" in self.model_fields_set:
            raise ValueError("role cannot be null")
        if self.is_active is None and "is_active" in self.model_fields_set:
            raise ValueError("is_active cannot be null")
        return self


class Granularity(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class UsersByRole(BaseModel):
    user: int
    manager: int
    admin: int


class UsersOverview(BaseModel):
    total: int
    active: int
    blocked: int
    by_role: UsersByRole


class RoutesOverview(BaseModel):
    total: int
    public: int
    hidden: int


class ComplaintsOverview(BaseModel):
    pending: int
    resolved: int
    rejected: int


class StatsOverview(BaseModel):
    users: UsersOverview
    routes: RoutesOverview
    complaints: ComplaintsOverview
    likes_total: int
    views_total: int
    generated_at: datetime


class SeriesPoint(BaseModel):
    period: date
    count: int


class TopRoute(BaseModel):
    route_id: int
    name: str
    views: int


class UsersStats(BaseModel):
    granularity: Granularity
    date_from: date
    date_to: date
    total: int
    series: list[SeriesPoint]


class ViewsStats(UsersStats):
    top_routes: list[TopRoute]


AdminUserOut = UserMe
