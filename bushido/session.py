"""Ride totals and the per-second CSV log of a session."""

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

from . import constants as c

CSV_COLUMNS = ["time", "elapsed_s", "speed_kmh", "power_w", "cadence_rpm", "force_n",
               "target_n", "mode", "slope_pct", "erg_w", "distance_m", "temperature_c",
               "latitude", "longitude", "altitude_m",
               "activity", "activity_time_s", "activity_pos"]


@dataclass
class Sample:
    speed_kmh: float
    power_w: float
    cadence_rpm: int
    force_n: float
    target_n: float
    mode: str
    slope_pct: float
    erg_w: float
    temperature_c: int | None
    latitude: float | None = None     # set while riding a GPX route
    longitude: float | None = None
    altitude_m: float | None = None
    activity: str | None = None       # workout or route file being ridden
    activity_time_s: float | None = None   # riding time since the activity started
    activity_pos: float | None = None      # metres along a route


class Session:
    """Accumulates moving time, distance and energy; writes one CSV row per second."""

    def __init__(self, directory: Path, now: datetime | None = None) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        started = now or datetime.now()
        self.path = directory / f"session_{started:%Y%m%d_%H%M%S}.csv"
        suffix = 2
        while self.path.exists():        # two sessions started within the same second
            self.path = directory / f"session_{started:%Y%m%d_%H%M%S}_{suffix}.csv"
            suffix += 1
        self._file: TextIO | None = self.path.open("w", newline="")
        self._writer = csv.writer(self._file)
        self._writer.writerow(CSV_COLUMNS)
        self.elapsed_s = 0.0
        self.distance_m = 0.0
        self.energy_j = 0.0
        self._since_row = 0.0

    @property
    def avg_power_w(self) -> float:
        return self.energy_j / self.elapsed_s if self.elapsed_s else 0.0

    def add(self, sample: Sample, dt: float, now: datetime | None = None,
            force_row: bool = False) -> bool:
        """Account for `dt` seconds at this sample; return whether a CSV row was written.

        Stopped time is not counted. `force_row` logs this very sample, e.g. the
        last point of a route, which would otherwise fall between two rows.
        """
        if self._file is None or sample.speed_kmh < c.MOVING_MIN_KMH:
            return False
        self.elapsed_s += dt
        self.distance_m += sample.speed_kmh / c.KMH_PER_MS * dt
        self.energy_j += sample.power_w * dt
        self._since_row += dt
        if self._since_row >= 1.0 or force_row:
            self._since_row = 0.0 if force_row else self._since_row - 1.0
            self._writer.writerow([
                (now or datetime.now()).isoformat(timespec="seconds"), round(self.elapsed_s),
                sample.speed_kmh, sample.power_w, sample.cadence_rpm, sample.force_n,
                round(sample.target_n, 1), sample.mode, sample.slope_pct, sample.erg_w,
                round(self.distance_m), sample.temperature_c,
                sample.latitude, sample.longitude,
                None if sample.altitude_m is None else round(sample.altitude_m, 1),
                sample.activity,
                None if sample.activity_time_s is None else round(sample.activity_time_s, 1),
                None if sample.activity_pos is None else round(sample.activity_pos, 1),
            ])
            self._file.flush()
            return True
        return False

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
