"""Workout and route files on disk, and the rider's saved settings."""

import json
import logging
from datetime import date
import re
from pathlib import Path
from typing import Any

from . import constants as c
from .physics import Settings
from .route import Route, parse_gpx
from .workout import Workout, parse_zwo

logger = logging.getLogger(__name__)

SAFE_NAME = re.compile(r"^[\w .()'+-]+\.(zwo|gpx)$", re.UNICODE)


class Library:
    def __init__(self, workouts_dir: Path, routes_dir: Path) -> None:
        self.workouts_dir, self.routes_dir = workouts_dir, routes_dir

    def _path(self, name: str) -> Path:
        """Resolve a file name inside the library. Rejects anything that is not a plain name."""
        if not SAFE_NAME.match(name) or ".." in name:
            raise ValueError(f"invalid file name {name!r}")
        return (self.workouts_dir if name.endswith(".zwo") else self.routes_dir) / name

    def workout(self, name: str) -> Workout:
        path = self._path(name)
        if not name.endswith(".zwo") or not path.is_file():
            raise ValueError(f"unknown workout {name!r}")
        return parse_zwo(path.read_text(encoding="utf-8"), path.stem)

    def route(self, name: str) -> Route:
        path = self._path(name)
        if not name.endswith(".gpx") or not path.is_file():
            raise ValueError(f"unknown route {name!r}")
        return parse_gpx(path.read_text(encoding="utf-8"), path.stem)

    def save(self, name: str, content: bytes) -> None:
        """Store an uploaded file after checking that it parses."""
        path = self._path(name)
        text = content.decode("utf-8")
        (parse_zwo if name.endswith(".zwo") else parse_gpx)(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def listing(self) -> dict[str, list[dict[str, Any]]]:
        workouts, routes = [], []
        for path in sorted(self.workouts_dir.glob("*.zwo")):
            try:
                w = self.workout(path.name)
            except ValueError as exc:
                logger.warning("Skipping %s: %s", path.name, exc)
                continue
            workouts.append({"file": path.name, "name": w.name, "duration_s": round(w.total_s),
                             "description": w.description,
                             "ftp_test": any(s.ftp_test for s in w.segments)})
        for path in sorted(self.routes_dir.glob("*.gpx")):
            try:
                r = self.route(path.name)
            except ValueError as exc:
                logger.warning("Skipping %s: %s", path.name, exc)
                continue
            routes.append({"file": path.name, "name": r.name, "distance_m": round(r.total_m),
                           "ascent_m": round(r.ascent_m)})
        return {"workouts": workouts, "routes": routes}


def workout_profile(workout: Workout) -> dict[str, Any]:
    """Segments as [start_s, duration_s, start_fraction, end_fraction] (None = free)."""
    rows, t = [], 0.0
    for s in workout.segments:
        rows.append([round(t), round(s.duration_s), s.start, s.end])
        t += s.duration_s
    return {"kind": "workout", "name": workout.name, "total_s": round(workout.total_s),
            "segments": rows}


def route_profile(route: Route) -> dict[str, Any]:
    """Evenly spaced [distance_m, elevation_m, lat, lon] points for drawing."""
    n = c.PROFILE_POINTS
    points = []
    for i in range(n + 1):
        d = route.total_m * i / n
        lat, lon = route.position(d)
        points.append([round(d), round(route.elevation(d), 1), round(lat, 6), round(lon, 6)])
    return {"kind": "route", "name": route.name, "total_m": round(route.total_m),
            "ascent_m": round(route.ascent_m), "points": points}


SETTING_LIMITS = {
    "mass_kg": (c.MASS_MIN_KG, c.MASS_MAX_KG),
    "inertia_kg": (c.INERTIA_MIN_KG, c.INERTIA_MAX_KG),
    "ftp_w": (c.FTP_MIN_W, c.FTP_MAX_W),
    "difficulty_pct": (c.DIFFICULTY_MIN_PCT, c.DIFFICULTY_MAX_PCT),
    "weekly_goal_km": (0.0, c.WEEKLY_GOAL_MAX_KM),
}


PROFILE_NAME = re.compile(r"^[\w .'-]{1,30}$", re.UNICODE)
DEFAULT_PROFILE = "Rider"


class ProfileStore:
    """Riders sharing the trainer: each has their settings and a lifetime odometer."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self.settings: dict[str, Settings] = {}
        self.odometer_m: dict[str, float] = {}
        self.calibration: dict[str, Any] | None = None   # last result: value_n, time
        self.events: dict[str, dict[str, str]] = {}      # the date each rider trains for
        self.ftp_history: dict[str, list[dict[str, Any]]] = {}
        self.active = DEFAULT_PROFILE
        self._load()
        if not self.settings:
            self.settings[DEFAULT_PROFILE] = Settings()
        if self.active not in self.settings:
            self.active = sorted(self.settings)[0]

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            saved = json.loads(self._path.read_text())
            profiles = saved["profiles"].items()
        except FileNotFoundError:
            return
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("Ignoring unreadable profiles file %s: %s", self._path, exc)
            return
        for name, values in profiles:
            if not PROFILE_NAME.match(name) or not isinstance(values, dict):
                continue
            settings = Settings()
            for key, (low, high) in SETTING_LIMITS.items():
                if isinstance(values.get(key), (int, float)):
                    setattr(settings, key, max(low, min(high, float(values[key]))))
            self.settings[name] = settings
            odometer = values.get("odometer_m")
            self.odometer_m[name] = float(odometer) if isinstance(odometer, (int, float)) else 0.0
            if isinstance(values.get("event"), dict):
                self.events[name] = values["event"]
            if isinstance(values.get("ftp_history"), list):
                self.ftp_history[name] = values["ftp_history"]
        if isinstance(saved.get("active"), str):
            self.active = saved["active"]
        if isinstance(saved.get("calibration"), dict):
            self.calibration = saved["calibration"]

    @property
    def current(self) -> Settings:
        return self.settings[self.active]

    def names(self) -> list[str]:
        return sorted(self.settings, key=str.lower)

    def create(self, name: str) -> None:
        """Add a rider, starting from default settings, and make them active."""
        name = name.strip()
        if not PROFILE_NAME.match(name):
            raise ValueError(f"invalid profile name {name!r}")
        if name.lower() in (existing.lower() for existing in self.settings):
            raise ValueError(f"profile {name!r} already exists")
        self.settings[name] = Settings()
        self.active = name
        self.save()

    def select(self, name: str) -> None:
        if name not in self.settings:
            raise ValueError(f"unknown profile {name!r}")
        self.active = name
        self.save()

    def set_event(self, name: str, day: str) -> None:
        """The event the active rider prepares; an empty name or date removes it."""
        name = name.strip()[:60]
        if not name or not day:
            self.events.pop(self.active, None)
        else:
            date.fromisoformat(day)                       # ValueError if not a date
            self.events[self.active] = {"name": name, "date": day}
        self.save()

    def record_ftp(self, ftp_w: float, today: date | None = None) -> None:
        """Keep the dated history of the active rider's FTP, one value per day."""
        day = (today or date.today()).isoformat()
        history = [h for h in self.ftp_history.get(self.active, []) if h["date"] != day]
        self.ftp_history[self.active] = history + [{"date": day, "ftp_w": round(ftp_w)}]

    def add_distance(self, metres: float) -> None:
        self.odometer_m[self.active] = self.odometer_m.get(self.active, 0.0) + metres

    def save(self) -> None:
        if self._path is None:
            return
        profiles = {
            name: {**{key: getattr(settings, key) for key in SETTING_LIMITS},
                   "odometer_m": round(self.odometer_m.get(name, 0.0)),
                   "event": self.events.get(name),
                   "ftp_history": self.ftp_history.get(name, [])}
            for name, settings in self.settings.items()
        }
        self._path.write_text(json.dumps(
            {"active": self.active, "calibration": self.calibration, "profiles": profiles},
            indent=2, ensure_ascii=False))
