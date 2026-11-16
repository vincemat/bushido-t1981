import pytest

from bushido import constants as c
from bushido.route import RouteRun, haversine_m, parse_gpx

M_PER_DEG_LAT = 111_195  # with the Earth radius used by haversine_m


def gpx(points, version="1.1"):
    ns = f"http://www.topografix.com/GPX/{version.replace('.', '/')}"
    body = "".join(f'<trkpt lat="{lat}" lon="{lon}"><ele>{ele}</ele></trkpt>'
                   for lat, lon, ele in points)
    return f'<gpx xmlns="{ns}"><trk><name>Col</name><trkseg>{body}</trkseg></trk></gpx>'


def straight_climb(length_m=2000, slope=0.05, step_m=20):
    return [(50 + d / M_PER_DEG_LAT, 5.0, 100 + slope * d) for d in range(0, length_m + 1, step_m)]


def test_haversine_one_degree_of_latitude():
    assert haversine_m(50, 5, 51, 5) == pytest.approx(M_PER_DEG_LAT, rel=1e-3)


def test_parse_distance_ascent_and_name():
    route = parse_gpx(gpx(straight_climb()))
    assert route.name == "Col"
    assert route.total_m == pytest.approx(2000, rel=1e-3)
    assert route.ascent_m == pytest.approx(100, abs=6)     # smoothing flattens both ends


def test_slope_in_the_middle_of_a_steady_climb():
    route = parse_gpx(gpx(straight_climb(slope=0.05)))
    assert route.slope_pct(1000) == pytest.approx(5.0, abs=0.1)


def test_slope_is_limited_to_the_simulated_range():
    route = parse_gpx(gpx(straight_climb(slope=0.30)))
    assert route.slope_pct(1000) == c.SLOPE_MAX_PCT


def test_gps_noise_is_smoothed():
    points = [(lat, lon, ele + (8 if i % 2 else -8))
              for i, (lat, lon, ele) in enumerate(straight_climb(slope=0.0))]
    assert abs(parse_gpx(gpx(points)).slope_pct(1000)) < 2.0


def test_duplicate_points_are_dropped_and_old_namespace_is_read():
    points = straight_climb()
    route = parse_gpx(gpx(points[:1] * 5 + points, version="1.0"))
    assert len(route.dist) == len(points)


def test_run_advances_and_finishes():
    run = RouteRun(parse_gpx(gpx(straight_climb())))
    run.advance(1000)
    lat, _ = run.route.position(run.distance_m)
    assert lat == pytest.approx(50 + 1000 / M_PER_DEG_LAT, abs=1e-5)
    assert run.route.elevation(run.distance_m) == pytest.approx(150, abs=1)
    run.advance(5000)
    assert run.finished


@pytest.mark.parametrize("text", ["nope", "<gpx/>",
                                  '<gpx><trk><trkseg><trkpt lat="1" lon="1"/><trkpt lat="2" lon="2"/></trkseg></trk></gpx>'])
def test_bad_files_raise_value_error(text):
    with pytest.raises(ValueError):
        parse_gpx(text)
