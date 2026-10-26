"""ORM models — a 1:1 implementation of the schema from the specification (section 5, 12)."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Role(StrEnum):
    USER = "user"
    MANAGER = "manager"
    ADMIN = "admin"


class ComplaintStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class User(Base):
    __tablename__ = "users"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint("role IN ('user', 'manager', 'admin')", name="role_valid"),
        # case-insensitive username uniqueness (prevents "Admin" vs "admin" impersonation)
        Index("uq_users_username_lower", text("lower(username)"), unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    avatar_url: Mapped[str] = mapped_column(String(500), nullable=False, server_default="")
    bio: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Route(Base):
    __tablename__ = "routes"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint("duration >= 0", name="duration_non_negative"),
        CheckConstraint("distance >= 0", name="distance_non_negative"),
        CheckConstraint("views_count >= 0", name="views_non_negative"),
        CheckConstraint("likes_count >= 0", name="likes_non_negative"),
        Index("idx_routes_author", "author_id"),
        Index("idx_routes_geometry", "route_geometry", postgresql_using="gist"),
        Index("idx_routes_public", "is_public"),
        Index("idx_routes_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    waypoints: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    route_geometry: Mapped[Any] = mapped_column(
        Geography(geometry_type="LINESTRING", srid=4326, spatial_index=False),
        nullable=False,
        deferred=True,
    )
    duration: Mapped[int] = mapped_column(Integer, nullable=False)
    distance: Mapped[int] = mapped_column(Integer, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    views_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    likes_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Tag(Base):
    __tablename__ = "tags"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False, server_default="#2a7fff")


class RouteTag(Base):
    __tablename__ = "route_tags"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (Index("idx_route_tags_tag", "tag_id"),)

    route_id: Mapped[int] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class Like(Base):
    __tablename__ = "likes"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (UniqueConstraint("user_id", "route_id", name="idx_likes_unique"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    route_id: Mapped[int] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Complaint(Base):
    __tablename__ = "complaints"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'resolved', 'rejected')", name="status_valid"),
        Index("idx_complaints_status", "status"),
        # one open complaint per (user, route): race-safe anti-spam
        Index(
            "uq_complaints_pending_user_route",
            "user_id",
            "route_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    route_id: Mapped[int] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RouteView(Base):
    __tablename__ = "route_views"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        Index("idx_views_date", "viewed_at"),
        Index("idx_views_route", "route_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    route_id: Mapped[int] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    viewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
