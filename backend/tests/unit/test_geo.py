from datetime import date

import pytest

from app.core.geo import (
    bucket_start,
    haversine_m,
    iter_buckets,
    path_length_m,
    point_ewkt,
    simplify_coords,
    to_linestring_wkt,
)


def test_haversine_known_distance():
    # 0.01 degrees of longitude at 52.37N is ~679 m
    assert haversine_m((4.89, 52.37), (4.90, 52.37)) == pytest.approx(679, abs=3)
    assert haversine_m((4.89, 52.37), (4.89, 52.37)) == 0


def test_path_length_is_sum_of_segments():
    pts = [(4.89, 52.37), (4.90, 52.37), (4.90, 52.38)]
    assert path_length_m(pts) == pytest.approx(
        haversine_m(pts[0], pts[1]) + haversine_m(pts[1], pts[2])
    )


def test_wkt_helpers_use_lng_lat_order():
    assert to_linestring_wkt([(4.8952, 52.3702), (4.8922, 52.3731)]) == (
        "LINESTRING(4.8952000 52.3702000, 4.8922000 52.3731000)"
    )
    assert point_ewkt(52.37, 4.89) == "SRID=4326;POINT(4.8900000 52.3700000)"


def test_simplify_drops_collinear_points_and_keeps_corners():
    line = [(0, 0), (1, 0.00001), (2, 0), (3, 5), (4, 0)]
    assert simplify_coords(line, 0.001) == [(0, 0), (2, 0), (3, 5), (4, 0)]


def test_simplify_keeps_endpoints_and_handles_short_lines():
    assert simplify_coords([(0, 0), (1, 1)], 10) == [(0, 0), (1, 1)]
    assert simplify_coords([(0, 0), (0.1, 0.0), (1, 0)], 10) == [(0, 0), (1, 0)]
    assert simplify_coords([(0, 0), (0.5, 0.2), (1, 0)], 0) == [(0, 0), (0.5, 0.2), (1, 0)]


@pytest.mark.parametrize(
    ("day", "granularity", "expected"),
    [
        (date(2026, 10, 28), "day", date(2026, 10, 28)),
        (date(2026, 10, 28), "week", date(2026, 10, 26)),  # Monday
        (date(2026, 10, 26), "week", date(2026, 10, 26)),
        (date(2026, 10, 28), "month", date(2026, 10, 1)),
    ],
)
def test_bucket_start(day, granularity, expected):
    assert bucket_start(day, granularity) == expected


def test_bucket_start_rejects_unknown_granularity():
    with pytest.raises(ValueError):
        bucket_start(date(2026, 1, 1), "year")


def test_iter_buckets_days_weeks_months():
    assert len(iter_buckets(date(2026, 10, 1), date(2026, 10, 30), "day")) == 30
    weeks = iter_buckets(date(2026, 10, 1), date(2026, 10, 30), "week")
    assert weeks[0] == date(2026, 9, 28) and weeks[-1] == date(2026, 10, 26) and len(weeks) == 5
    months = iter_buckets(date(2026, 11, 15), date(2027, 2, 3), "month")
    assert months == [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)]
