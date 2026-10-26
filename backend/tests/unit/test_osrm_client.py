import httpx
import pytest

from app.core.errors import ServiceUnavailableError, UnprocessableError
from app.services.osrm import OSRMClient

POINTS = [(52.37, 4.89), (52.38, 4.90)]  # (lat, lng)


def client_for(handler) -> OSRMClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OSRMClient("http://osrm:5000/", http, "foot")


def ok_body(**route):
    geometry = {"coordinates": [[4.89, 52.37], [4.9, 52.38]]}
    base = {"distance": 1234.4, "duration": 900.6, "geometry": geometry}
    return {"code": "Ok", "routes": [{**base, **route}]}


async def test_builds_url_with_lng_lat_order_and_parses_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        return httpx.Response(200, json=ok_body())

    route = await client_for(handler).route(POINTS)
    assert seen["path"] == "/route/v1/foot/4.890000,52.370000;4.900000,52.380000"
    assert seen["query"]["geometries"] == "geojson" and seen["query"]["overview"] == "full"
    assert route.distance_m == 1234 and route.duration_s == 901
    assert route.coordinates == [(4.89, 52.37), (4.9, 52.38)]


@pytest.mark.parametrize("code", ["NoRoute", "NoSegment"])
async def test_no_route_maps_to_422(code):
    handler = lambda r: httpx.Response(400, json={"code": code, "message": "x"})  # noqa: E731
    with pytest.raises(UnprocessableError) as exc:
        await client_for(handler).route(POINTS)
    assert exc.value.code == "route_not_found"


async def test_invalid_query_maps_to_422():
    body = {"code": "InvalidQuery", "message": "x"}
    handler = lambda r: httpx.Response(400, json=body)  # noqa: E731
    with pytest.raises(UnprocessableError):
        await client_for(handler).route(POINTS)


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(500, text="boom"),
        lambda r: httpx.Response(200, text="<html>"),
        lambda r: httpx.Response(200, json={"code": "Ok", "routes": []}),
        lambda r: httpx.Response(200, json={"code": "Ok", "routes": [{"distance": "x"}]}),
    ],
)
async def test_upstream_failures_map_to_503(handler):
    with pytest.raises(ServiceUnavailableError) as exc:
        await client_for(handler).route(POINTS)
    assert exc.value.code == "routing_unavailable"


async def test_network_errors_map_to_503():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ServiceUnavailableError):
        await client_for(handler).route(POINTS)


async def test_degenerate_geometry_is_rejected():
    body = ok_body(geometry={"coordinates": [[4.89, 52.37]]})
    handler = lambda r: httpx.Response(200, json=body)  # noqa: E731
    with pytest.raises(UnprocessableError):
        await client_for(handler).route(POINTS)
