"""What past sessions tell: power records, weekly distance, and ghosts to race.

Everything is derived from the session CSV files (one row per ridden second);
a `.json` file next to each CSV caches its summary.
"""

import bisect
import csv
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import constants as c

logger = logging.getLogger(__name__)

SUMMARY_VERSION = 1


class PowerRecords:
    """Best average power over fixed windows, fed one sample per ridden second."""

    def __init__(self, baseline: dict[int, float] | None = None) -> None:
        self.best: dict[int, float] = {w: 0.0 for w in c.RECORD_WINDOWS_S}
        self._baseline = baseline or {}
        self._prefix = [0.0]
        self._announced: set[int] = set()

    def add_second(self, power_w: float) -> list[tuple[int, int]]:
        """Add one second; return (window, watts) for each record beaten for the first time.

        A window with no previous record is not announced: there is nothing to beat.
        """
        self._prefix.append(self._prefix[-1] + power_w)
        seconds, beaten = len(self._prefix) - 1, []
        for window in c.RECORD_WINDOWS_S:
            if seconds < window:
                continue
            average = (self._prefix[-1] - self._prefix[-1 - window]) / window
            self.best[window] = max(self.best[window], average)
            previous = self._baseline.get(window)
            if previous and average > previous and window not in self._announced:
                self._announced.add(window)
                beaten.append((window, round(average)))
        return beaten


def summarise(csv_path: Path) -> dict[str, Any] | None:
    """Summary of one session, cached next to it. None for an empty or unreadable file."""
    cache = csv_path.with_suffix(".json")
    try:
        cached = json.loads(cache.read_text())
        if cached.get("version") == SUMMARY_VERSION and cache.stat().st_mtime >= csv_path.stat().st_mtime:
            return cached
    except (OSError, ValueError):
        pass
    try:
        with csv_path.open(newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return None
        records = PowerRecords()
        for row in rows:
            records.add_second(float(row["power_w"]))
        summary = {
            "version": SUMMARY_VERSION,
            "file": csv_path.name,
            "profile": csv_path.parent.name,
            "start": rows[0]["time"],
            "elapsed_s": int(rows[-1]["elapsed_s"]),
            "distance_m": int(rows[-1]["distance_m"]),
            "avg_power_w": round(sum(float(r["power_w"]) for r in rows) / len(rows)),
            "best_power": {str(w): round(v) for w, v in records.best.items() if v},
            "activities": sorted({r["activity"] for r in rows if r.get("activity")}),
        }
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("Cannot summarise %s: %s", csv_path, exc)
        return None
    cache.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def summaries(sessions_dir: Path, profile: str | None = None) -> list[dict[str, Any]]:
    pattern = f"{profile}/session_*.csv" if profile else "*/session_*.csv"
    found = (summarise(path) for path in sorted(sessions_dir.glob(pattern)))
    return [s for s in found if s is not None]


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def profile_stats(sessions: list[dict[str, Any]], goal_km: float,
                  today: date | None = None) -> dict[str, Any]:
    """Records, this week's distance and goal streak from one profile's summaries."""
    today = today or date.today()
    best: dict[int, int] = {}
    weekly_m: dict[date, float] = {}
    for s in sessions:
        for window, watts in s["best_power"].items():
            best[int(window)] = max(best.get(int(window), 0), watts)
        week = _week_start(datetime.fromisoformat(s["start"]).date())
        weekly_m[week] = weekly_m.get(week, 0.0) + s["distance_m"]
    this_week = _week_start(today)
    streak = 0
    if goal_km > 0:
        week = this_week if weekly_m.get(this_week, 0.0) >= goal_km * 1000 \
            else this_week - timedelta(days=7)      # the current week may still be completed
        while weekly_m.get(week, 0.0) >= goal_km * 1000:
            streak += 1
            week -= timedelta(days=7)
    return {
        "sessions": len(sessions),
        "total_km": round(sum(s["distance_m"] for s in sessions) / 1000, 1),
        "best_power": {str(w): best.get(w) for w in c.RECORD_WINDOWS_S},
        "longest_km": round(max((s["distance_m"] for s in sessions), default=0) / 1000, 1),
        "week_km": round(weekly_m.get(this_week, 0.0) / 1000, 1),
        "goal_km": goal_km,
        "streak_weeks": streak,
    }


@dataclass(frozen=True)
class Ghost:
    """A past ride of a route: where it was after each second of riding."""

    profile: str
    start: str
    times: tuple[float, ...]
    positions: tuple[float, ...]

    @property
    def total_s(self) -> float:
        return self.times[-1]

    def position_at(self, time_s: float) -> float:
        if time_s >= self.times[-1]:
            return self.positions[-1]
        i = bisect.bisect_right(self.times, time_s)
        if i == 0:
            return 0.0
        share = (time_s - self.times[i - 1]) / (self.times[i] - self.times[i - 1])
        return self.positions[i - 1] + share * (self.positions[i] - self.positions[i - 1])

    def time_at(self, position_m: float) -> float:
        """Riding time at which the ghost reached this point of the route."""
        if position_m >= self.positions[-1]:
            return self.times[-1]
        i = bisect.bisect_right(self.positions, position_m)
        if i == 0:
            return 0.0
        span = self.positions[i] - self.positions[i - 1]
        share = (position_m - self.positions[i - 1]) / span if span else 0.0
        return self.times[i - 1] + share * (self.times[i] - self.times[i - 1])


def _read_ghost(csv_path: Path, activity: str, total_m: float) -> Ghost | None:
    """The first complete ride of `activity` in a session, or None if it was never finished."""
    times, positions = [0.0], [0.0]
    try:
        with csv_path.open(newline="") as f:
            for row in csv.DictReader(f):
                if row.get("activity") != activity or not row.get("activity_pos"):
                    continue
                time_s, position = float(row["activity_time_s"]), float(row["activity_pos"])
                if position < positions[-1] or time_s <= times[-1]:
                    times, positions = [0.0], [0.0]      # the route was restarted
                if time_s > 0:
                    times.append(time_s)
                    positions.append(position)
                if position >= total_m - c.GHOST_FINISH_TOLERANCE_M:
                    return Ghost(csv_path.parent.name, row["time"], tuple(times), tuple(positions))
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("Cannot read ghost from %s: %s", csv_path, exc)
    return None


def best_ghosts(sessions_dir: Path, activity: str, total_m: float) -> dict[str, Ghost]:
    """Fastest complete ride of a route for each profile that finished it."""
    best: dict[str, Ghost] = {}
    for summary in summaries(sessions_dir):
        if activity not in summary["activities"]:
            continue
        ghost = _read_ghost(sessions_dir / summary["profile"] / summary["file"], activity, total_m)
        if ghost and (ghost.profile not in best or ghost.total_s < best[ghost.profile].total_s):
            best[ghost.profile] = ghost
    return best


# --- Outdoor rides and progress ------------------------------------------------
# Rides done on the road are typed in by the rider; they count for distance and
# time, not for power records (there is no power data).

def _outdoor_path(sessions_dir: Path, profile: str) -> Path:
    return sessions_dir / profile / "outdoor.json"


def load_outdoor(sessions_dir: Path, profile: str) -> list[dict[str, Any]]:
    try:
        rides = json.loads(_outdoor_path(sessions_dir, profile).read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable outdoor rides of %s: %s", profile, exc)
        return []
    return rides if isinstance(rides, list) else []


def _save_outdoor(sessions_dir: Path, profile: str, rides: list[dict[str, Any]]) -> None:
    path = _outdoor_path(sessions_dir, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rides, indent=2, ensure_ascii=False))


def add_outdoor(sessions_dir: Path, profile: str, day: date, distance_m: float,
                elapsed_s: float, ascent_m: float, note: str) -> dict[str, Any]:
    rides = load_outdoor(sessions_dir, profile)
    ride = {"id": max((r["id"] for r in rides), default=0) + 1, "date": day.isoformat(),
            "distance_m": round(distance_m), "elapsed_s": round(elapsed_s),
            "ascent_m": round(ascent_m), "note": note.strip()}
    _save_outdoor(sessions_dir, profile, rides + [ride])
    return ride


def delete_outdoor(sessions_dir: Path, profile: str, ride_id: int) -> bool:
    rides = load_outdoor(sessions_dir, profile)
    kept = [r for r in rides if r["id"] != ride_id]
    if len(kept) == len(rides):
        return False
    _save_outdoor(sessions_dir, profile, kept)
    return True


def all_rides(sessions_dir: Path, profile: str) -> list[dict[str, Any]]:
    """Trainer sessions and outdoor rides of a profile, in one shape, oldest first."""
    outdoor = [{"outdoor": True, "id": r["id"], "start": r["date"] + "T12:00:00",
                "distance_m": r["distance_m"], "elapsed_s": r["elapsed_s"],
                "ascent_m": r["ascent_m"], "note": r["note"], "best_power": {},
                "avg_power_w": None, "activities": []}
               for r in load_outdoor(sessions_dir, profile)]
    return sorted(summaries(sessions_dir, profile) + outdoor, key=lambda r: r["start"])


def weekly_progress(rides: list[dict[str, Any]], weeks: int,
                    today: date | None = None) -> list[dict[str, Any]]:
    """Distance, time and best efforts of each of the last `weeks` weeks, oldest first."""
    this_week = _week_start(today or date.today())
    series = []
    for back in range(weeks - 1, -1, -1):
        start = this_week - timedelta(weeks=back)
        mine = [r for r in rides
                if _week_start(datetime.fromisoformat(r["start"]).date()) == start]
        best = lambda window: max((r["best_power"].get(str(window), 0) for r in mine), default=0)
        series.append({
            "start": start.isoformat(),
            "indoor_km": round(sum(r["distance_m"] for r in mine if not r.get("outdoor")) / 1000, 1),
            "outdoor_km": round(sum(r["distance_m"] for r in mine if r.get("outdoor")) / 1000, 1),
            "hours": round(sum(r["elapsed_s"] for r in mine) / 3600, 1),
            "ascent_m": round(sum(r.get("ascent_m", 0) for r in mine)),
            "best_300_w": best(300) or None,
            "best_1200_w": best(1200) or None,
        })
    return series
