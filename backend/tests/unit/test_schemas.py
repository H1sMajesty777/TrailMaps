import pytest
from pydantic import ValidationError

from app.schemas.auth import RegisterRequest
from app.schemas.routes import BuildRouteRequest, RouteCreate, RouteUpdate
from app.schemas.tags import TagCreate
from app.schemas.users import UserUpdate

OK = {"email": "User@Example.com", "username": "walker_1", "password": "Passw0rd!x"}


def test_register_normalizes_email_and_phone():
    req = RegisterRequest(**OK, phone="+31 (6) 1234-5678")
    assert req.email == "user@example.com"
    assert req.phone == "+31612345678"


@pytest.mark.parametrize(
    "override",
    [
        {"password": "short1"},
        {"password": "onlyletters"},
        {"password": "12345678"},
        {"password": "a1" * 40},  # > 72 bytes: bcrypt would silently truncate
        {"username": "ab"},
        {"username": "has space"},
        {"username": "x" * 31},
        {"email": "not-an-email"},
        {"phone": "abc"},
    ],
)
def test_register_rejects_invalid_input(override):
    with pytest.raises(ValidationError):
        RegisterRequest(**{**OK, **override})


def test_unicode_usernames_are_allowed():
    assert RegisterRequest(**{**OK, "username": "Иван_1"}).username == "Иван_1"


def test_user_update_requires_a_field_and_forbids_null_username():
    with pytest.raises(ValidationError):
        UserUpdate()
    with pytest.raises(ValidationError):
        UserUpdate(username=None)
    update = UserUpdate(phone="")
    assert update.phone is None and "phone" in update.model_fields_set


def test_waypoint_bounds_and_counts():
    ok = {"lat": 52.0, "lng": 4.0}
    assert len(BuildRouteRequest(waypoints=[ok, ok]).waypoints) == 2
    with pytest.raises(ValidationError):
        BuildRouteRequest(waypoints=[ok])
    with pytest.raises(ValidationError):
        BuildRouteRequest(waypoints=[ok] * 26)
    for bad in ({"lat": 91, "lng": 0}, {"lat": 0, "lng": 181}, {"lat": float("nan"), "lng": 0}):
        with pytest.raises(ValidationError):
            BuildRouteRequest(waypoints=[ok, bad])


def test_route_create_validation():
    wp = [{"lat": 52.0, "lng": 4.0}, {"lat": 52.1, "lng": 4.1}]
    assert RouteCreate(name="  Walk  ", waypoints=wp).name == "Walk"
    with pytest.raises(ValidationError):
        RouteCreate(name="   ", waypoints=wp)
    with pytest.raises(ValidationError):
        RouteCreate(name="x", waypoints=wp, tag_ids=list(range(11)))


def test_route_update_rejects_empty_and_null_fields():
    with pytest.raises(ValidationError):
        RouteUpdate()
    with pytest.raises(ValidationError):
        RouteUpdate(name=None)
    assert RouteUpdate(is_public=False).is_public is False


def test_tag_color_validation():
    assert TagCreate(name="x").color == "#2a7fff"
    with pytest.raises(ValidationError):
        TagCreate(name="x", color="red")
