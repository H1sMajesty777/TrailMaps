"""Async client for the OSRM routing engine."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.errors import ServiceUnavailableError, UnprocessableError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OSRMRoute:
    coordinates: list[tuple[float, float]]  # (lng, lat)
    distance_m: int
    duration_s: int


class OSRMClient:
    def __init__(self, base_url: str, client: httpx.AsyncClient, profile: str = "foot") -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._profile = profile

    async def route(self, points: Sequence[tuple[float, float]]) -> OSRMRoute:
        """Build a route through `points` given as (lat, lng) pairs."""
        coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
        url = f"{self._base_url}/route/v1/{self._profile}/{coords}"
        params = {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
            "alternatives": "false",
        }
        try:
            response = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            logger.warning("OSRM request failed: %s", exc.__class__.__name__)
            raise ServiceUnavailableError(
                "Routing service is unavailable", code="routing_unavailable"
            ) from exc

        data = self._safe_json(response)
        code = data.get("code") if isinstance(data, dict) else None

        if response.status_code >= 500:
            raise ServiceUnavailableError("Routing service error", code="routing_unavailable")
        if code in ("NoRoute", "NoSegment"):
            raise UnprocessableError(
                "No walkable route between the given points", code="route_not_found"
            )
        if response.status_code != 200 or code != "Ok":
            if 400 <= response.status_code < 500:
                raise UnprocessableError("Routing request was rejected", code="route_not_found")
            raise ServiceUnavailableError("Unexpected routing response", code="routing_unavailable")

        try:
            best = data["routes"][0]
            coordinates = [(float(c[0]), float(c[1])) for c in best["geometry"]["coordinates"]]
            distance = int(round(float(best["distance"])))
            duration = int(round(float(best["duration"])))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("Malformed OSRM response")
            raise ServiceUnavailableError(
                "Malformed routing response", code="routing_unavailable"
            ) from exc
        if len(coordinates) < 2:
            raise UnprocessableError("Route geometry is degenerate", code="route_not_found")
        return OSRMRoute(coordinates=coordinates, distance_m=distance, duration_s=duration)

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None
