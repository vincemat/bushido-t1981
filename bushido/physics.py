"""Turn a riding mode (slope, target power...) into the force sent to the brake.

The brake only applies the force it is told. A constant force feels jerky
because nothing replaces the rider's momentum, so the target is recomputed
from the measured speed on every control tick, as the T1982 head unit did.
"""

from dataclasses import dataclass
from enum import Enum

from . import constants as c


class Mode(str, Enum):
    FREE = "free"      # minimum resistance
    SLOPE = "slope"    # road simulation at a given gradient
    ERG = "erg"        # constant power whatever the speed
    FORCE = "force"    # raw force, for tests


@dataclass
class Settings:
    mass_kg: float = c.DEFAULT_MASS_KG
    inertia_kg: float = c.DEFAULT_INERTIA_KG
    ftp_w: float = c.DEFAULT_FTP_W
    difficulty_pct: float = c.DEFAULT_DIFFICULTY_PCT
    weekly_goal_km: float = c.DEFAULT_WEEKLY_GOAL_KM


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def low_speed_factor(speed_ms: float) -> float:
    """0 below LOW_SPEED_ZERO_KMH, 1 above LOW_SPEED_FULL_KMH, linear between."""
    kmh = speed_ms * c.KMH_PER_MS
    span = c.LOW_SPEED_FULL_KMH - c.LOW_SPEED_ZERO_KMH
    return clamp((kmh - c.LOW_SPEED_ZERO_KMH) / span, 0.0, 1.0)


def road_force(speed_ms: float, slope_pct: float, mass_kg: float) -> float:
    """Rolling + aerodynamic + gravity resistance of a real ride, in N."""
    weight = mass_kg * c.GRAVITY
    return (c.ROLLING_COEFF * weight
            + c.AERO_COEFF * speed_ms ** 2
            + weight * slope_pct / 100.0)


def erg_force(speed_ms: float, power_w: float) -> float:
    return power_w / max(speed_ms, c.ERG_MIN_SPEED_MS)


class Controller:
    """Stateful force computation: mode targets, inertia term, limits, smoothing."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.mode = Mode.FREE
        self.slope_pct = 0.0
        self.erg_w = 100.0
        self.force_n = 0.0
        self.target_n = 0.0          # last force handed to the brake
        self._accel = 0.0
        self._last_speed: tuple[float, float] | None = None  # (timestamp, m/s)

    def observe_speed(self, timestamp: float, speed_ms: float) -> None:
        """Feed each new speed sample (about 1 per second) to estimate acceleration."""
        if self._last_speed is not None:
            dt = timestamp - self._last_speed[0]
            if dt <= 0:
                return
            raw = clamp((speed_ms - self._last_speed[1]) / dt,
                        -c.MAX_ACCEL_MS2, c.MAX_ACCEL_MS2)
            self._accel += c.ACCEL_FILTER_ALPHA * (raw - self._accel)
        self._last_speed = (timestamp, speed_ms)

    def reset(self) -> None:
        """Forget speed history and target, e.g. when the brake went to sleep."""
        self._accel = 0.0
        self._last_speed = None
        self.target_n = 0.0

    def _wanted_force(self, speed_ms: float) -> float:
        if self.mode is Mode.FORCE:
            return clamp(self.force_n, 0.0, c.MAX_FORCE_N)
        if self.mode is Mode.FREE:
            return 0.0
        if self.mode is Mode.SLOPE:
            base = road_force(speed_ms, self.slope_pct, self.settings.mass_kg)
        else:
            base = erg_force(speed_ms, self.erg_w)
        # Virtual momentum: ease off while the rider slows down, push back
        # while accelerating. Speed arrives at ~1 Hz, so a large inertia_kg
        # can oscillate: it is a tunable, not a physical truth.
        base += self.settings.inertia_kg * self._accel
        return clamp(base, 0.0, c.MAX_FORCE_N) * low_speed_factor(speed_ms)

    def step(self, speed_ms: float, dt: float) -> float:
        """Force to send now, in N, moving at most MAX_FORCE_RATE towards the goal."""
        wanted = self._wanted_force(speed_ms)
        max_delta = c.MAX_FORCE_RATE_N_PER_S * dt
        self.target_n += clamp(wanted - self.target_n, -max_delta, max_delta)
        return self.target_n
