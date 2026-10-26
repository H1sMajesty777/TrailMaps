from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import ComplaintStatus


class ComplaintCreate(BaseModel):
    route_id: int
    description: str = Field(min_length=3, max_length=2000)


class ComplaintUpdate(BaseModel):
    status: ComplaintStatus
    hide_route: bool | None = None


class ComplaintOut(BaseModel):
    id: int
    route_id: int
    route_name: str
    user_id: int
    username: str
    description: str
    status: ComplaintStatus
    created_at: datetime
