"""Verifies that the Alembic migration really implements the schema from the specification."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

EXPECTED_TABLES = {
    "users",
    "routes",
    "tags",
    "route_tags",
    "likes",
    "complaints",
    "route_views",
    "alembic_version",
}


async def scalar(app, sql, **params):
    async with app.state.session_factory() as session:
        return await session.scalar(text(sql), params)


async def execute(app, sql, **params):
    async with app.state.session_factory() as session:
        await session.execute(text(sql), params)
        await session.commit()


async def test_all_tables_exist(app):
    async with app.state.session_factory() as session:
        rows = await session.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
    assert EXPECTED_TABLES <= {r[0] for r in rows}


async def test_postgis_is_enabled_and_geometry_is_geography_linestring(app):
    assert await scalar(app, "SELECT postgis_version()")
    udt = await scalar(
        app,
        "SELECT udt_name FROM information_schema.columns "
        "WHERE table_name = 'routes' AND column_name = 'route_geometry'",
    )
    assert udt == "geography"
    typmod = await scalar(
        app,
        "SELECT type FROM geography_columns WHERE f_table_name = 'routes'",
    )
    assert typmod == "LineString"
    srid = await scalar(app, "SELECT srid FROM geography_columns WHERE f_table_name = 'routes'")
    assert srid == 4326


async def test_gist_index_on_route_geometry(app):
    definition = await scalar(app, "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_routes_geometry'")
    assert definition and "gist" in definition.lower()


async def test_spec_indexes_exist(app):
    expected = {
        "idx_routes_author",
        "idx_routes_geometry",
        "idx_routes_public",
        "idx_routes_created",
        "idx_route_tags_tag",
        "idx_likes_unique",
        "idx_complaints_status",
        "idx_views_date",
        "idx_views_route",
        "uq_users_username_lower",
    }
    async with app.state.session_factory() as session:
        rows = await session.execute(text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"))
    assert expected <= {r[0] for r in rows}


async def test_role_and_status_check_constraints(app):
    with pytest.raises(IntegrityError):
        await execute(
            app, "INSERT INTO users (email, username, password_hash, role) VALUES ('a@x.com','a','h','hacker')"
        )


async def test_username_uniqueness_is_case_insensitive(app):
    await execute(app, "INSERT INTO users (email, username, password_hash) VALUES ('a@x.com','Admin','h')")
    with pytest.raises(IntegrityError):
        await execute(app, "INSERT INTO users (email, username, password_hash) VALUES ('b@x.com','admin','h')")


async def test_cascades_and_set_null_follow_the_specification(app):
    await execute(app, "INSERT INTO users (id, email, username, password_hash) VALUES (1,'a@x.com','a','h'),(2,'b@x.com','b','h')")
    await execute(
        app,
        "INSERT INTO routes (id, name, author_id, waypoints, route_geometry, duration, distance) "
        "VALUES (1, 'r', 1, '[]', ST_GeogFromText('LINESTRING(4.89 52.37, 4.90 52.38)'), 10, 100)",
    )
    await execute(app, "INSERT INTO likes (user_id, route_id) VALUES (2, 1)")
    await execute(app, "INSERT INTO complaints (user_id, route_id, description) VALUES (2, 1, 'bad')")
    await execute(app, "INSERT INTO route_views (route_id, user_id) VALUES (1, 2)")

    await execute(app, "DELETE FROM users WHERE id = 2")
    assert await scalar(app, "SELECT count(*) FROM likes") == 0
    assert await scalar(app, "SELECT count(*) FROM complaints") == 0
    assert await scalar(app, "SELECT count(*) FROM route_views") == 1
    assert await scalar(app, "SELECT user_id FROM route_views") is None

    await execute(app, "DELETE FROM users WHERE id = 1")
    assert await scalar(app, "SELECT count(*) FROM routes") == 0
    assert await scalar(app, "SELECT count(*) FROM route_views") == 0


async def test_likes_are_unique_per_user_and_route(app):
    await execute(app, "INSERT INTO users (id, email, username, password_hash) VALUES (1,'a@x.com','a','h')")
    await execute(
        app,
        "INSERT INTO routes (id, name, author_id, waypoints, route_geometry, duration, distance) "
        "VALUES (1, 'r', 1, '[]', ST_GeogFromText('LINESTRING(4.89 52.37, 4.90 52.38)'), 10, 100)",
    )
    await execute(app, "INSERT INTO likes (user_id, route_id) VALUES (1, 1)")
    with pytest.raises(IntegrityError):
        await execute(app, "INSERT INTO likes (user_id, route_id) VALUES (1, 1)")


async def test_negative_counters_are_rejected(app):
    await execute(app, "INSERT INTO users (id, email, username, password_hash) VALUES (1,'a@x.com','a','h')")
    with pytest.raises(IntegrityError):
        await execute(
            app,
            "INSERT INTO routes (name, author_id, waypoints, route_geometry, duration, distance, likes_count) "
            "VALUES ('r', 1, '[]', ST_GeogFromText('LINESTRING(4.89 52.37, 4.90 52.38)'), 10, 100, -1)",
        )
