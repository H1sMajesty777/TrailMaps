"""Shared test helpers: fake OSRM server, user factories, request shortcuts."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

import httpx

from app.core.geo import path_length_m
from app.core.security import hash_password
from app.db.models import User
from app.services.osrm import OSRMClient

PASSWORD = "Passw0rd!x"

# Amsterdam (Dam square area) and Utrecht — ~35 km apart
AMS_A = {"lat": 52.3702, "lng": 4.8952}
AMS_B = {"lat": 52.3731, "lng": 4.8922}
UTR_A = {"lat": 52.0907, "lng": 5.1214}
UTR_B = {"lat": 52.1007, "lng": 5.1214}
UTR_C = {"lat": 52.1007, "lng": 5.1414}
AMS_CENTER = {"lat": 52.3702, "lng": 4.8952}


class FakeOSRM:
    """In-process OSRM replacement served through httpx.MockTransport."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.calls = 0
        self._clients: list[httpx.AsyncClient] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.mode == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if self.mode == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if self.mode == "server_error":
            return httpx.Response(500, text="boom")
        if self.mode == "no_route":
            return httpx.Response(400, json={"code": "NoRoute", "message": "Impossible route"})
        if self.mode == "no_segment":
            return httpx.Response(400, json={"code": "NoSegment", "message": "No segment"})
        if self.mode == "garbage":
            return httpx.Response(200, json={"code": "Ok", "routes": []})
        if self.mode == "not_json":
            return httpx.Response(200, text="<html>nope</html>")

        raw = unquote(request.url.path).rsplit("/", 1)[-1]
        points = [tuple(float(x) for x in pair.split(",")) for pair in raw.split(";")]
        geometry: list[tuple[float, float]] = []
        for start, end in zip(points, points[1:], strict=False):
            geometry.append(start)
            geometry.append(((start[0] + end[0]) / 2, (start[1] + end[1]) / 2))
        geometry.append(points[-1])
        distance = path_length_m(geometry)
        body = {
            "code": "Ok",
            "routes": [
                {
                    "distance": distance,
                    "duration": distance / 1.4,
                    "geometry": {"type": "LineString", "coordinates": [list(p) for p in geometry]},
                }
            ],
            "waypoints": [],
        }
        return httpx.Response(200, json=body)

    def make_client(self, base_url: str, profile: str) -> OSRMClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        self._clients.append(http)
        return OSRMClient(base_url, http, profile)

    async def aclose(self) -> None:
        for client in self._clients:
            await client.aclose()


@dataclass
class Actor:
    id: int
    email: str
    username: str
    role: str
    tokens: dict[str, Any]

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens['access_token']}"}


async def create_user_in_db(
    app,
    *,
    email: str,
    username: str,
    role: str = "user",
    is_active: bool = True,
    password: str = PASSWORD,
) -> int:
    async with app.state.session_factory() as session:
        user = User(
            email=email,
            username=username,
            password_hash=hash_password(password, 4),
            role=role,
            is_active=is_active,
        )
        session.add(user)
        await session.commit()
        return user.id


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


async def make_actor(app, client: httpx.AsyncClient, name: str, role: str = "user") -> Actor:
    email = f"{name}@example.com"
    user_id = await create_user_in_db(app, email=email, username=name, role=role)
    tokens = await login(client, email)
    return Actor(id=user_id, email=email, username=name, role=role, tokens=tokens)


def route_payload(
    name: str = "Morning walk",
    waypoints: list[dict[str, float]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {"name": name, "waypoints": waypoints or [AMS_A, AMS_B], **extra}


async def create_route(
    client: httpx.AsyncClient, actor: Actor, name: str = "Morning walk", **kwargs: Any
) -> dict[str, Any]:
    response = await client.post(
        "/api/routes", json=route_payload(name, **kwargs), headers=actor.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


