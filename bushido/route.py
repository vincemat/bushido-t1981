"""Real routes from GPX files: the simulated gradient follows the elevation profile."""

import bisect
import math
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass

from . import constants as c


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * c.EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _interpolate(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x)
    share = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
    return ys[i - 1] + share * (ys[i] - ys[i - 1])


@dataclass(frozen=True)
class Route:
    name: str
    dist: tuple[float, ...]     # cumulative distance of each point, m
    ele: tuple[float, ...]      # smoothed elevation, m
    lat: tuple[float, ...]
    lon: tuple[float, ...]

    @property
    def total_m(self) -> float:
        return self.dist[-1]

    @property
    def ascent_m(self) -> float:
        return sum(max(b - a, 0.0) for a, b in zip(self.ele, self.ele[1:]))

    def elevation(self, distance_m: float) -> float:
        return _interpolate(self.dist, self.ele, distance_m)

    def position(self, distance_m: float) -> tuple[float, float]:
        return (_interpolate(self.dist, self.lat, distance_m),
                _interpolate(self.dist, self.lon, distance_m))

    def slope_pct(self, distance_m: float) -> float:
        """Gradient around a point, over ROUTE_SLOPE_WINDOW_M, within the simulated range."""
        half = c.ROUTE_SLOPE_WINDOW_M / 2
        a = max(distance_m - half, 0.0)
        b = min(distance_m + half, self.total_m)
        if b <= a:
            return 0.0
        slope = (self.elevation(b) - self.elevation(a)) / (b - a) * 100
        return max(c.SLOPE_MIN_PCT, min(c.SLOPE_MAX_PCT, slope))


def _smooth(dist: list[float], ele: list[float], window_m: float) -> list[float]:
    """Moving average of the elevation over +/- window_m / 2 of distance."""
    out, lo, hi, total = [], 0, 0, 0.0
    for d in dist:
        while hi < len(dist) and dist[hi] <= d + window_m / 2:
            total += ele[hi]
            hi += 1
        while dist[lo] < d - window_m / 2:
            total -= ele[lo]
            lo += 1
        out.append(total / (hi - lo))
    return out


def parse_gpx(text: str, fallback_name: str = "Route") -> Route:
    """Parse the track (or route) points of a GPX file. Raises ValueError if unusable."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"not a valid .gpx file: {exc}") from exc
    local = lambda el: el.tag.rsplit("}", 1)[-1]  # GPX 1.0 and 1.1 use different namespaces
    points = [el for el in root.iter() if local(el) == "trkpt"] \
        or [el for el in root.iter() if local(el) == "rtept"]
    dist: list[float] = []
    ele: list[float] = []
    lat: list[float] = []
    lon: list[float] = []
    for point in points:
        elevation = next((child.text for child in point if local(child) == "ele"), None)
        if elevation is None:
            raise ValueError("the GPX track has no elevation data")
        p_lat, p_lon = float(point.attrib["lat"]), float(point.attrib["lon"])
        if lat:
            step = haversine_m(lat[-1], lon[-1], p_lat, p_lon)
            if step < c.ROUTE_MIN_POINT_SPACING_M:
                continue
            dist.append(dist[-1] + step)
        else:
            dist.append(0.0)
        lat.append(p_lat)
        lon.append(p_lon)
        ele.append(float(elevation))
    if len(dist) < 2:
        raise ValueError("the GPX file holds fewer than two distinct points")
    name = next((el.text for el in root.iter() if local(el) == "name" and el.text), fallback_name)
    return Route(name.strip(), tuple(dist), tuple(_smooth(dist, ele, c.ROUTE_SMOOTH_WINDOW_M)),
                 tuple(lat), tuple(lon))


class RouteRun:
    """Position along a route while riding it."""

    def __init__(self, route: Route) -> None:
        self.route = route
        self.distance_m = 0.0
        self.elapsed_s = 0.0

    @property
    def finished(self) -> bool:
        return self.distance_m >= self.route.total_m

    def advance(self, delta_m: float, dt: float = 0.0) -> None:
        self.distance_m = min(self.distance_m + delta_m, self.route.total_m)
        self.elapsed_s += dt

    def slope_pct(self) -> float:
        return self.route.slope_pct(self.distance_m)
