"""Structured workouts: power targets over time, read from Zwift .zwo files.

Powers in a .zwo file are fractions of the rider's FTP. For ramps (Warmup,
Cooldown, Ramp) PowerLow is the starting value and PowerHigh the final one.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    duration_s: float
    start: float | None     # fraction of FTP; None = free ride (no target)
    end: float | None
    label: str
    ftp_test: bool = False  # free-ride step whose average power estimates the FTP

    def fraction_at(self, t: float) -> float | None:
        if self.start is None or self.end is None:
            return None
        return self.start + (self.end - self.start) * min(max(t / self.duration_s, 0.0), 1.0)


@dataclass(frozen=True)
class Workout:
    name: str
    description: str
    segments: tuple[Segment, ...]

    @property
    def total_s(self) -> float:
        return sum(s.duration_s for s in self.segments)


def _number(element: ET.Element, *names: str) -> float:
    for name in names:
        if name in element.attrib:
            return float(element.attrib[name])
    raise ValueError(f"<{element.tag}> lacks {' / '.join(names)}")


def parse_zwo(text: str, fallback_name: str = "Workout") -> Workout:
    """Parse a Zwift workout file. Raises ValueError when it holds no usable step."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"not a valid .zwo file: {exc}") from exc
    steps = root.find("workout")
    if steps is None:
        raise ValueError("no <workout> element")
    segments: list[Segment] = []
    for el in steps:
        tag = el.tag
        if tag == "SteadyState":
            power = _number(el, "Power", "PowerLow")
            segments.append(Segment(_number(el, "Duration"), power, power, "Effort"))
        elif tag in ("Warmup", "Cooldown", "Ramp"):
            label = {"Warmup": "Échauffement", "Cooldown": "Retour au calme", "Ramp": "Rampe"}[tag]
            segments.append(Segment(_number(el, "Duration"), _number(el, "PowerLow"),
                                    _number(el, "PowerHigh"), label))
        elif tag == "IntervalsT":
            repeat = int(_number(el, "Repeat"))
            on = _number(el, "OnPower", "PowerOnHigh")
            off = _number(el, "OffPower", "PowerOffHigh")
            for i in range(repeat):
                segments.append(Segment(_number(el, "OnDuration"), on, on,
                                        f"Intervalle {i + 1}/{repeat}"))
                segments.append(Segment(_number(el, "OffDuration"), off, off,
                                        f"Récupération {i + 1}/{repeat}"))
        elif tag in ("FreeRide", "Freeride", "MaxEffort"):
            test = el.attrib.get("ftptest") == "1"   # attribute used by Zwift's own FTP tests
            segments.append(Segment(_number(el, "Duration"), None, None,
                                    "Test FTP : à fond, régulier" if test else "Libre", test))
        # other tags (text events, unsupported steps) are ignored
    segments = [s for s in segments if s.duration_s > 0]
    if not segments:
        raise ValueError("no usable step in the workout")
    name = (root.findtext("name") or fallback_name).strip()
    return Workout(name, (root.findtext("description") or "").strip(), tuple(segments))


class WorkoutRun:
    """Position in a workout while riding it."""

    def __init__(self, workout: Workout, ftp_w: float) -> None:
        self.workout = workout
        self.ftp_w = ftp_w
        self.elapsed_s = 0.0

    @property
    def finished(self) -> bool:
        return self.elapsed_s >= self.workout.total_s

    def advance(self, dt: float) -> None:
        self.elapsed_s = min(self.elapsed_s + dt, self.workout.total_s)

    def position(self) -> tuple[int, float]:
        """(segment index, seconds into it). On the last segment's end when finished."""
        t = self.elapsed_s
        for index, segment in enumerate(self.workout.segments):
            if t < segment.duration_s:
                return index, t
            t -= segment.duration_s
        last = len(self.workout.segments) - 1
        return last, self.workout.segments[last].duration_s

    def skip(self) -> None:
        """Jump to the start of the next segment."""
        index, into = self.position()
        self.elapsed_s = min(self.elapsed_s - into + self.workout.segments[index].duration_s,
                             self.workout.total_s)

    def target_w(self) -> float | None:
        index, into = self.position()
        fraction = self.workout.segments[index].fraction_at(into)
        return None if fraction is None else fraction * self.ftp_w
