"""Glue between the brake link, the force controller and the session log."""

import csv
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import constants as c
from .calibration import Calibrator
from .export import csv_to_tcx
from .history import (Ghost, PowerRecords, add_outdoor, all_rides, best_ghosts, delete_outdoor,
                      profile_stats, summarise, weekly_progress)
from .library import SETTING_LIMITS, Library, ProfileStore, route_profile, workout_profile
from .link import STATUS_CONNECTED, BrakeLink
from .protocol import BrakeData, encode_calibration_command, encode_target_force
from .physics import Controller, Mode, clamp
from .route import RouteRun
from .session import Sample, Session
from .workout import WorkoutRun

logger = logging.getLogger(__name__)


class Trainer:
    def __init__(self, link: BrakeLink, sessions_dir: Path, captures_dir: Path | None = None,
                 library: Library | None = None, settings_path: Path | None = None) -> None:
        self.link = link
        self.profiles = ProfileStore(settings_path)
        self.controller = Controller(self.profiles.current)
        self.trip_m = 0.0                 # distance ridden since the app or the profile started
        self._unsaved_odometer_s = 0.0
        self.library = library
        self.workout: WorkoutRun | None = None
        self.route: RouteRun | None = None
        self.activity_file: str | None = None
        self.last_export: str | None = None
        self.ftp_result: dict[str, int] | None = None
        self.ghost: Ghost | None = None
        self.events: list[dict[str, Any]] = []     # records beaten, goal reached
        self.last_session: dict[str, Any] | None = None   # what the summary screen shows
        self._session_events: list[dict[str, Any]] = []
        self._session_activity: str | None = None
        self.finished_activity: str | None = None   # shown until the rider moves on
        self._event_id = 0
        self._records: PowerRecords | None = None
        self._week_before_m = 0.0                  # this week's distance before the session
        self._goal_announced = False
        self._free_step: int | None = None    # index of the free-ride step being ridden
        self._test_time_s = 0.0
        self._test_energy_j = 0.0
        self.session: Session | None = None
        self._sessions_dir = sessions_dir
        self._captures_dir = captures_dir or sessions_dir
        self.calibrator: Calibrator | None = None
        self.calibration_value_n: float | None = None
        self._calibration_log: Path | None = None
        self._last_calibration_row: tuple[str | None, int | None] | None = None
        self._last_speed_ts = 0.0
        self._was_connected = False

    def tick(self, dt: float) -> dict[str, Any]:
        """One control step: read the brake, send the new target, log, report."""
        state = self.link.snapshot()
        connected = state.status == STATUS_CONNECTED
        data = state.data
        speed_kmh = data.speed_kmh or 0.0
        if connected:
            if state.speed_ts != self._last_speed_ts:
                self._last_speed_ts = state.speed_ts
                self.controller.observe_speed(state.speed_ts, speed_kmh / c.KMH_PER_MS)
            if self.calibrator is not None and not self.calibrator.finished:
                # No target force during calibration: the brake runs its own procedure.
                target = 0.0
                self._calibration_step(data)
            else:
                if speed_kmh >= c.MOVING_MIN_KMH:
                    self._advance_activity(speed_kmh / c.KMH_PER_MS, dt, data.power_w or 0)
                target = self.controller.step(speed_kmh / c.KMH_PER_MS, dt)
                self.link.send_page(encode_target_force(target))
            if speed_kmh >= c.MOVING_MIN_KMH:
                self._count_distance(speed_kmh / c.KMH_PER_MS * dt, dt)
            if self.session is not None:
                sample = Sample(
                    speed_kmh, data.power_w or 0, data.cadence_rpm or 0, data.force_n or 0.0,
                    target, self.controller.mode.value, self.controller.slope_pct,
                    self.controller.erg_w, data.temperature_c)
                if self.route is not None:
                    run = self.route
                    sample.latitude, sample.longitude = run.route.position(run.distance_m)
                    sample.altitude_m = run.route.elevation(run.distance_m)
                    sample.activity, sample.activity_time_s = self.activity_file, run.elapsed_s
                    sample.activity_pos = run.distance_m
                elif self.workout is not None:
                    sample.activity = self.activity_file
                    sample.activity_time_s = self.workout.elapsed_s
                finished = self.route is not None and self.route.finished
                if self.session.add(sample, dt, force_row=finished):
                    self._after_logged_second(sample.power_w)
            if self.route is not None and self.route.finished:
                self._finish_route()
                self.finished_activity = self.route.route.name
                self.route = self.ghost = None
        elif self._was_connected:
            self.controller.reset()
        self._was_connected = connected
        return self._report(state.status, connected, data)

    def _count_distance(self, metres: float, dt: float) -> None:
        self.trip_m += metres
        self.profiles.add_distance(metres)
        self._unsaved_odometer_s += dt
        if self._unsaved_odometer_s >= c.ODOMETER_SAVE_EVERY_S:
            self._unsaved_odometer_s = 0.0
            self.profiles.save()

    def _sessions_folder(self) -> Path:
        return self._sessions_dir / self.profiles.active

    def switch_profile(self, name: str | None, create: bool = False) -> None:
        """Change rider: the running session and activity belong to the previous one."""
        self.stop_session()
        self.stop_activity()
        if create:
            self.profiles.create(name)
        else:
            self.profiles.select(name)
        self.controller.settings = self.profiles.current
        self.trip_m = 0.0
        self.last_export = self.ftp_result = self.last_session = None

    def _advance_activity(self, speed_ms: float, dt: float, power_w: float) -> None:
        """Move along the workout or route and set the controller's mode and target."""
        ctl = self.controller
        if self.workout is not None:
            run = self.workout
            before = run.position()[0]
            if run.workout.segments[before].ftp_test:
                self._test_time_s += dt
                self._test_energy_j += power_w * dt
            run.advance(dt)
            index = None if run.finished else run.position()[0]
            if index != before:
                self._leave_step(before)
            if index is None:
                self._emit("workout_finished", name=run.workout.name)
                self.finished_activity = run.workout.name
                # Keep a gentle road under the wheel rather than no resistance at all.
                ctl.mode, ctl.slope_pct, self.workout = Mode.SLOPE, 0.0, None
            elif run.target_w() is not None:
                ctl.mode, ctl.erg_w = Mode.ERG, clamp(run.target_w(), c.ERG_MIN_W, c.ERG_MAX_W)
            elif self._free_step != index:
                # Free-ride step: simulate a road so the rider can pace with the gears;
                # the slope stays theirs to adjust until the step ends.
                self._free_step = index
                ctl.mode, ctl.slope_pct = Mode.SLOPE, c.FREE_RIDE_SLOPE_PCT
        elif self.route is not None:
            self.route.advance(speed_ms * dt, dt)
            if self.route.finished:
                # tick() logs the last point, then closes the route
                ctl.mode, ctl.slope_pct = Mode.SLOPE, 0.0
            else:
                ctl.mode = Mode.SLOPE
                ctl.slope_pct = self.route.slope_pct() * ctl.settings.difficulty_pct / 100

    def _emit(self, kind: str, **details: Any) -> None:
        self._event_id += 1
        event = {"id": self._event_id, "type": kind, **details}
        self.events = (self.events + [event])[-c.EVENTS_KEPT:]
        if self.session is not None:
            self._session_events.append(event)

    def _begin_session(self) -> None:
        """Start recording, remembering what there is to beat this time."""
        profile = self.profiles.active
        stats = profile_stats(all_rides(self._sessions_dir, profile),
                              self.controller.settings.weekly_goal_km)
        self._records = PowerRecords({int(w): v for w, v in stats["best_power"].items() if v})
        self._week_before_m = stats["week_km"] * 1000
        self._goal_announced = False
        self._session_events, self._session_activity = [], None
        self.last_session = self.ftp_result = None
        self.session = Session(self._sessions_folder())

    def _after_logged_second(self, power_w: float) -> None:
        for window, watts in self._records.add_second(power_w):
            self._emit("record", window_s=window, watts=watts)
        goal_m = self.controller.settings.weekly_goal_km * 1000
        if goal_m > 0 and not self._goal_announced \
                and self._week_before_m < goal_m <= self._week_before_m + self.session.distance_m:
            self._goal_announced = True
            self._emit("goal", km=self.controller.settings.weekly_goal_km)

    def _finish_route(self) -> None:
        run, ghost = self.route, self.ghost
        self._emit("route_finished", name=run.route.name, time_s=round(run.elapsed_s),
                   ghost_profile=None if ghost is None else ghost.profile,
                   ghost_gap_s=None if ghost is None else round(ghost.total_s - run.elapsed_s))

    def ghost_candidates(self, name: str) -> list[dict[str, Any]]:
        """Fastest finished ride of a route per profile, fastest first."""
        if self.library is None or not name.endswith(".gpx"):
            return []
        total = self.library.route(name).total_m
        ghosts = best_ghosts(self._sessions_dir, name, total).values()
        return [{"profile": g.profile, "time_s": round(g.total_s), "date": g.start[:10]}
                for g in sorted(ghosts, key=lambda g: g.total_s)]

    def stats(self) -> dict[str, Any]:
        """Records, weekly distance and goal of every profile, for the leaderboard."""
        return {name: profile_stats(all_rides(self._sessions_dir, name),
                                    self.profiles.settings[name].weekly_goal_km)
                for name in self.profiles.names()}

    def progress(self, today: date | None = None) -> dict[str, Any]:
        """Everything the progress page shows for the active rider."""
        today = today or date.today()
        profile, settings = self.profiles.active, self.controller.settings
        rides = all_rides(self._sessions_dir, profile)
        stats = profile_stats(rides, settings.weekly_goal_km, today)
        event = self.profiles.events.get(profile)
        if event:
            days = (date.fromisoformat(event["date"]) - today).days
            event = {**event, "days_left": days}
        return {
            "profile": profile, "stats": stats, "ftp_w": settings.ftp_w,
            "week": {"km": stats["week_km"], "goal_km": settings.weekly_goal_km,
                     "remaining_km": round(max(settings.weekly_goal_km - stats["week_km"], 0), 1),
                     "days_left": 7 - today.weekday()},
            "weeks": weekly_progress(rides, c.PROGRESS_WEEKS, today),
            "ftp_history": self.profiles.ftp_history.get(profile, []),
            "event": event,
            "rides": rides[-c.RECENT_RIDES:][::-1],
        }

    def add_outdoor_ride(self, day: date, km: float, minutes: float, ascent_m: float,
                         note: str) -> dict[str, Any]:
        return add_outdoor(self._sessions_dir, self.profiles.active, day, km * 1000,
                           minutes * 60, ascent_m, note)

    def delete_outdoor_ride(self, ride_id: int) -> bool:
        return delete_outdoor(self._sessions_dir, self.profiles.active, ride_id)

    def _leave_step(self, index: int) -> None:
        """Close a workout step; a completed FTP-test step yields an FTP estimate."""
        segment = self.workout.workout.segments[index]
        self._free_step = None
        if segment.ftp_test:
            if self._test_time_s >= c.FTP_TEST_MIN_COMPLETION * segment.duration_s:
                average = self._test_energy_j / self._test_time_s
                self.ftp_result = {"average_w": round(average),
                                   "ftp_w": round(average * c.FTP_TEST_FACTOR)}
            self._test_time_s = self._test_energy_j = 0.0

    def _activity_report(self) -> dict[str, Any] | None:
        if self.workout is not None:
            run, segments = self.workout, self.workout.workout.segments
            index, into = run.position()
            following = segments[index + 1] if index + 1 < len(segments) else None
            target = run.target_w()
            return {
                "kind": "workout", "file": self.activity_file, "name": run.workout.name,
                "elapsed_s": round(run.elapsed_s), "total_s": round(run.workout.total_s),
                "step": index + 1, "steps": len(segments), "label": segments[index].label,
                "step_remaining_s": round(segments[index].duration_s - into),
                "target_w": None if target is None else round(target),
                "ftp_test": segments[index].ftp_test,
                "test_average_w": round(self._test_energy_j / self._test_time_s)
                if self._test_time_s else None,
                "next_label": None if following is None else following.label,
                "next_w": None if following is None or following.start is None
                else round(following.start * run.ftp_w),
            }
        if self.route is not None:
            run = self.route
            lat, lon = run.route.position(run.distance_m)
            ghost = None
            if self.ghost is not None:
                ghost_m = self.ghost.position_at(run.elapsed_s)
                g_lat, g_lon = run.route.position(ghost_m)
                ghost = {
                    "profile": self.ghost.profile, "distance_m": round(ghost_m),
                    "lat": g_lat, "lon": g_lon,
                    # positive = the rider is ahead of the ghost
                    "gap_m": round(run.distance_m - ghost_m),
                    "gap_s": round(self.ghost.time_at(run.distance_m) - run.elapsed_s),
                }
            return {
                "ghost": ghost, "elapsed_s": round(run.elapsed_s),
                "kind": "route", "file": self.activity_file, "name": run.route.name,
                "distance_m": round(run.distance_m), "total_m": round(run.route.total_m),
                "slope_pct": round(run.slope_pct(), 1),
                "elevation_m": round(run.route.elevation(run.distance_m)),
                "lat": lat, "lon": lon,
            }
        return None

    def profile(self, name: str) -> dict[str, Any]:
        """Data to draw a workout or a route. Raises ValueError for an unknown file."""
        if self.library is None:
            raise ValueError("no library configured")
        if name.endswith(".zwo"):
            return workout_profile(self.library.workout(name))
        return route_profile(self.library.route(name))

    def start_activity(self, name: str, ghost_profile: str | None = None) -> None:
        if self.library is None:
            raise ValueError("no library configured")
        self.workout = self.route = self.ghost = None
        self.ftp_result = self._free_step = self.finished_activity = None
        self._test_time_s = self._test_energy_j = 0.0
        if name.endswith(".zwo"):
            self.workout = WorkoutRun(self.library.workout(name), self.controller.settings.ftp_w)
        else:
            self.route = RouteRun(self.library.route(name))
            if ghost_profile:
                self.ghost = best_ghosts(self._sessions_dir, name,
                                         self.route.route.total_m).get(ghost_profile)
        self.activity_file = name
        if self.session is None:
            self._begin_session()
        run = self.workout or self.route
        self._session_activity = run.workout.name if self.workout else run.route.name

    def stop_activity(self) -> None:
        if self.workout is not None or self.route is not None:
            self.workout = self.route = self.ghost = None
            self.controller.mode, self.controller.slope_pct = Mode.SLOPE, 0.0

    def _calibration_step(self, data: BrakeData) -> None:
        calibrator = self.calibrator
        command = calibrator.update(data.calibration_index, data.calibration_status,
                                    data.calibration_value_n)
        if command is not None:
            self.link.send_page(encode_calibration_command(command))
        row = (data.calibration_raw, command)
        if row != self._last_calibration_row and self._calibration_log is not None:
            # Raw trace of every change: this part of the protocol is still being verified.
            self._last_calibration_row = row
            with self._calibration_log.open("a", newline="") as f:
                csv.writer(f).writerow([
                    datetime.now().isoformat(timespec="milliseconds"), data.calibration_raw,
                    "" if command is None else f"{command:02x}", calibrator.phase.value,
                    data.speed_kmh])
        if calibrator.finished:
            self.calibration_value_n = calibrator.value_n
            if calibrator.value_n is not None:
                self.profiles.calibration = {"value_n": calibrator.value_n,
                                             "time": datetime.now().isoformat(timespec="seconds")}
                self.profiles.save()
            self.controller.reset()
            logger.info("Calibration %s, value %s N", calibrator.phase.value, calibrator.value_n)

    def start_calibration(self) -> None:
        self._captures_dir.mkdir(parents=True, exist_ok=True)
        self._calibration_log = self._captures_dir / f"calibration_{datetime.now():%Y%m%d_%H%M%S}.csv"
        with self._calibration_log.open("w", newline="") as f:
            csv.writer(f).writerow(["time", "page22_hex", "command_sent", "phase", "speed_kmh"])
        self._last_calibration_row = None
        self.controller.reset()
        self.calibrator = Calibrator()

    def _report(self, status: str, connected: bool, data: BrakeData) -> dict[str, Any]:
        ctl, session = self.controller, self.session
        return {
            "brake": {
                "status": status,
                "speed_kmh": data.speed_kmh if connected else 0.0,
                "power_w": data.power_w if connected else 0,
                "cadence_rpm": data.cadence_rpm if connected else 0,
                "force_n": data.force_n if connected else 0.0,
                "temperature_c": data.temperature_c,
                "alarm": data.alarm or 0,
            },
            "control": {
                "mode": ctl.mode.value, "slope_pct": ctl.slope_pct, "erg_w": ctl.erg_w,
                "force_n": ctl.force_n, "target_n": round(ctl.target_n, 1),
            },
            "settings": {key: getattr(ctl.settings, key) for key in SETTING_LIMITS},
            "profile": {"active": self.profiles.active, "names": self.profiles.names(),
                        "odometer_km": round(self.profiles.odometer_m.get(self.profiles.active, 0.0)
                                             / 1000, 1)},
            "trip_m": round(self.trip_m),
            "activity": self._activity_report(),
            "last_export": self.last_export,
            "ftp_result": self.ftp_result,
            "events": self.events,
            "calibration": {
                "phase": None if self.calibrator is None else self.calibrator.phase.value,
                "value_n": self.calibration_value_n,
                "raw": data.calibration_raw,
                "last": self.profiles.calibration,
            },
            "version": c.APP_VERSION,
            "last_session": self.last_session,
            "finished_activity": self.finished_activity,
            "session": None if session is None else {
                "elapsed_s": round(session.elapsed_s), "distance_m": round(session.distance_m),
                "avg_power_w": round(session.avg_power_w),
                "energy_kj": round(session.energy_j / 1000, 1), "file": session.path.name,
            },
        }

    def handle(self, message: dict[str, Any]) -> None:
        """Apply one command from the UI. Unknown or malformed commands raise ValueError."""
        if not isinstance(message, dict):
            raise ValueError(f"malformed command {message!r}")
        cmd, ctl = message.get("cmd"), self.controller
        try:
            if cmd == "mode":
                ctl.mode = Mode(message["mode"])
            elif cmd == "slope":
                ctl.slope_pct = clamp(float(message["value"]), c.SLOPE_MIN_PCT, c.SLOPE_MAX_PCT)
            elif cmd == "erg":
                ctl.erg_w = clamp(float(message["value"]), c.ERG_MIN_W, c.ERG_MAX_W)
            elif cmd == "force":
                ctl.force_n = clamp(float(message["value"]), 0.0, c.MAX_FORCE_N)
            elif cmd == "setting":
                low, high = SETTING_LIMITS[message["key"]]
                setattr(ctl.settings, message["key"], clamp(float(message["value"]), low, high))
                if message["key"] == "ftp_w":
                    self.profiles.record_ftp(ctl.settings.ftp_w)
                if self.workout is not None:
                    self.workout.ftp_w = ctl.settings.ftp_w
                self.profiles.save()
            elif cmd == "event_set":
                self.profiles.set_event(str(message["name"]), str(message["date"]))
            elif cmd == "profile_select":
                self.switch_profile(message["name"])
            elif cmd == "profile_create":
                self.switch_profile(message["name"], create=True)
            elif cmd == "activity_start":
                self.start_activity(message["file"], message.get("ghost"))
            elif cmd == "activity_stop":
                self.stop_activity()
            elif cmd == "ftp_result_clear":
                self.ftp_result = None
            elif cmd == "summary_close":
                self.last_session = None
            elif cmd == "activity_skip":
                if self.workout is not None:
                    index = self.workout.position()[0]
                    self.workout.skip()
                    self._leave_step(index)     # a skipped FTP test gives no result
            elif cmd == "calibration_start":
                self.start_calibration()
            elif cmd == "calibration_cancel":
                self.calibrator = None
            elif cmd == "session_start":
                if self.session is None:
                    self._begin_session()
            elif cmd == "session_stop":
                self.stop_session()
            else:
                raise ValueError(f"unknown command {cmd!r}")
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"malformed command {message!r}") from exc

    def stop_session(self) -> None:
        if self.session is None:
            return
        session, self.session = self.session, None
        self.finished_activity = None
        session.close()
        self.stop_activity()
        if session.elapsed_s < c.MIN_SESSION_S:
            session.path.unlink(missing_ok=True)      # nothing worth keeping
            self.last_export = None
            return
        exported = csv_to_tcx(session.path)
        summarise(session.path)
        self.last_export = None if exported is None else f"{exported.parent.name}/{exported.name}"
        self.last_session = {
            "activity": self._session_activity, "elapsed_s": round(session.elapsed_s),
            "distance_m": round(session.distance_m), "avg_power_w": round(session.avg_power_w),
            "energy_kj": round(session.energy_j / 1000), "export": self.last_export,
            "events": self._session_events, "ftp_result": self.ftp_result,
        }
        logger.info("Session saved to %s (export: %s)", session.path, self.last_export)

    def shutdown(self) -> None:
        """Release the brake: it keeps applying its last target otherwise."""
        self.stop_session()
        self.profiles.save()
        for _ in range(c.SHUTDOWN_ZERO_REPEATS):
            self.link.send_page(encode_target_force(0.0))
            time.sleep(1 / c.CONTROL_HZ)
        self.link.stop()
