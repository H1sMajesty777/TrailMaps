"""Initial schema: users, routes, tags, route_tags, likes, complaints, route_views.

Revision ID: 0001
Revises:
Create Date: 2026-10-26
"""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("avatar_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("bio", sa.Text(), nullable=False, server_default=""),
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.CheckConstraint("role IN ('user', 'manager', 'admin')", name="ck_users_role_valid"),
    )
    op.create_index("uq_users_username_lower", "users", [sa.text("lower(username)")], unique=True)

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("color", sa.String(7), nullable=False, server_default="#2a7fff"),
        sa.PrimaryKeyConstraint("id", name="pk_tags"),
        sa.UniqueConstraint("name", name="uq_tags_name"),
    )

    op.create_table(
        "routes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("waypoints", postgresql.JSONB(), nullable=False),
        sa.Column(
            "route_geometry",
            Geography(geometry_type="LINESTRING", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.Column("distance", sa.Integer(), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("views_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("likes_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id", name="pk_routes"),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name="fk_routes_author_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint("duration >= 0", name="ck_routes_duration_non_negative"),
        sa.CheckConstraint("distance >= 0", name="ck_routes_distance_non_negative"),
        sa.CheckConstraint("views_count >= 0", name="ck_routes_views_non_negative"),
        sa.CheckConstraint("likes_count >= 0", name="ck_routes_likes_non_negative"),
    )
    op.create_index("idx_routes_author", "routes", ["author_id"])
    op.create_index("idx_routes_geometry", "routes", ["route_geometry"], postgresql_using="gist")
    op.create_index("idx_routes_public", "routes", ["is_public"])
    op.create_index("idx_routes_created", "routes", ["created_at"])

    op.create_table(
        "route_tags",
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("route_id", "tag_id", name="pk_route_tags"),
        sa.ForeignKeyConstraint(
            ["route_id"], ["routes.id"], name="fk_route_tags_route_id_routes", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["tags.id"], name="fk_route_tags_tag_id_tags", ondelete="CASCADE"
        ),
    )
    op.create_index("idx_route_tags_tag", "route_tags", ["tag_id"])

    op.create_table(
        "likes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id", name="pk_likes"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_likes_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["route_id"], ["routes.id"], name="fk_likes_route_id_routes", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("user_id", "route_id", name="idx_likes_unique"),
    )

    op.create_table(
        "complaints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id", name="pk_complaints"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_complaints_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["route_id"], ["routes.id"], name="fk_complaints_route_id_routes", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'resolved', 'rejected')", name="ck_complaints_status_valid"
        ),
    )
    op.create_index("idx_complaints_status", "complaints", ["status"])
    op.create_index(
        "uq_complaints_pending_user_route",
        "complaints",
        ["user_id", "route_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "route_views",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id", name="pk_route_views"),
        sa.ForeignKeyConstraint(
            ["route_id"], ["routes.id"], name="fk_route_views_route_id_routes", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_route_views_user_id_users", ondelete="SET NULL"
        ),
    )
    op.create_index("idx_views_date", "route_views", ["viewed_at"])
    op.create_index("idx_views_route", "route_views", ["route_id"])

    # Default tags from the specification (section 3.1)
    tags = sa.table("tags", sa.column("name", sa.String), sa.column("color", sa.String))
    op.bulk_insert(
        tags,
        [
            {"name": "еда", "color": "#ff7a45"},
            {"name": "развлечения", "color": "#9254de"},
            {"name": "прогулка", "color": "#36cfc9"},
            {"name": "исторический", "color": "#d4a017"},
        ],
    )


def downgrade() -> None:
    op.drop_table("route_views")
    op.drop_table("complaints")
    op.drop_table("likes")
    op.drop_table("route_tags")
    op.drop_table("routes")
    op.drop_table("tags")
    op.drop_table("users")
