import csv
from datetime import date, datetime, timedelta

import pytest

from bushido.history import Ghost, PowerRecords, best_ghosts, profile_stats, summaries, summarise
from bushido.session import CSV_COLUMNS
from tests.test_library_export import FakeLink, library, ride, trainer  # noqa: F401 (fixtures)


def write_session(folder, name, start, powers, speed_kmh=36.0, activity=None, lap_m=None):
    """One row per second at constant speed; `lap_m` restarts the route position."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"session_{name}.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for i, power in enumerate(powers, start=1):
            metres = speed_kmh / 3.6 * i
            w.writerow({"time": (start + timedelta(seconds=i)).isoformat(timespec="seconds"),
                        "elapsed_s": i, "speed_kmh": speed_kmh, "power_w": power,
                        "cadence_rpm": 80, "distance_m": round(metres),
                        "activity": activity or "", "activity_time_s": i if activity else "",
                        "activity_pos": round(metres, 1) if activity and activity.endswith(".gpx") else ""})
    return path


def test_power_records_rolling_best():
    records = PowerRecords()
    for power in [100] * 60 + [300] * 60 + [100] * 200:
        records.add_second(power)
    assert records.best[60] == 300
    assert records.best[300] == pytest.approx((300 * 60 + 100 * 240) / 300)
    assert records.best[1200] == 0                     # the ride is shorter than 20 minutes


def test_record_is_announced_once_and_only_against_a_previous_one():
    records = PowerRecords({60: 200})
    beaten = [b for power in [250] * 200 for b in records.add_second(power)]
    assert beaten == [(60, 250)]                       # no 5-minute record existed to beat
    assert [b for power in [400] * 60 for b in records.add_second(power)] == []


def test_summary_is_cached_and_refreshed(tmp_path):
    path = write_session(tmp_path / "Rider", "a", datetime(2026, 11, 9, 18), [200] * 120,
                         activity="col.gpx")
    summary = summarise(path)
    assert summary["profile"] == "Rider" and summary["distance_m"] == 1200
    assert summary["best_power"] == {"60": 200} and summary["activities"] == ["col.gpx"]
    assert path.with_suffix(".json").exists()
    assert summarise(path) == summary


def test_old_sessions_without_activity_columns_are_read(tmp_path):
    path = tmp_path / "Rider" / "session_old.csv"
    path.parent.mkdir()
    path.write_text("time,elapsed_s,speed_kmh,power_w,cadence_rpm,distance_m\n"
                    "2026-11-15T21:00:00,1,20,100,80,6\n")
    assert summarise(path)["activities"] == []


def test_empty_session_has_no_summary(tmp_path):
    path = tmp_path / "s.csv"
    path.write_text(",".join(CSV_COLUMNS) + "\n")
    assert summarise(path) is None


def test_profile_stats_week_goal_and_streak(tmp_path):
    folder = tmp_path / "Rider"
    monday = datetime(2026, 11, 9, 18)                 # weeks start on Monday
    for weeks_ago, seconds in [(0, 1500), (1, 3000), (2, 2500), (4, 4000)]:
        write_session(folder, f"w{weeks_ago}", monday - timedelta(weeks=weeks_ago),
                      [150 + 20 * weeks_ago] * seconds)
    stats = profile_stats(summaries(tmp_path, "Rider"), goal_km=20, today=date(2026, 11, 12))
    assert stats["week_km"] == 15.0 and stats["sessions"] == 4
    assert stats["streak_weeks"] == 2                  # last two weeks; week 3 breaks it
    assert stats["best_power"] == {"60": 230, "300": 230, "1200": 230}
    assert stats["longest_km"] == 40.0
    done = profile_stats(summaries(tmp_path, "Rider"), goal_km=15, today=date(2026, 11, 12))
    assert done["streak_weeks"] == 3                   # the current week counts once reached
    assert profile_stats([], goal_km=0)["streak_weeks"] == 0


def test_ghost_interpolation():
    ghost = Ghost("V", "2026-11-01", (0.0, 10.0, 20.0), (0.0, 100.0, 150.0))
    assert ghost.position_at(5) == 50 and ghost.position_at(15) == 125
    assert ghost.position_at(99) == 150 and ghost.total_s == 20
    assert ghost.time_at(50) == 5 and ghost.time_at(125) == 15 and ghost.time_at(999) == 20


def test_best_ghost_per_profile_ignores_unfinished_rides(tmp_path):
    start = datetime(2026, 11, 1, 10)
    write_session(tmp_path / "Rider", "slow", start, [150] * 400, 18.0, "col.gpx")   # 2000 m
    write_session(tmp_path / "Rider", "fast", start, [250] * 200, 36.0, "col.gpx")
    write_session(tmp_path / "Rider", "dnf", start, [300] * 100, 36.0, "col.gpx")    # 1000 m
    write_session(tmp_path / "Alex", "m", start, [200] * 250, 28.8, "col.gpx")
    write_session(tmp_path / "Alex", "other", start, [200] * 250, 36.0, "autre.gpx")
    ghosts = best_ghosts(tmp_path, "col.gpx", total_m=2000)
    assert {p: g.total_s for p, g in ghosts.items()} == {"Rider": 200, "Alex": 250}


def test_racing_a_ghost_then_becoming_one(trainer, tmp_path):  # noqa: F811
    # Alex rode the 2 km climb at 18 km/h earlier.
    write_session(tmp_path / "sessions" / "Alex", "m", datetime(2026, 11, 1, 10), [150] * 400,
                  18.0, "col.gpx")
    (candidate,) = trainer.ghost_candidates("col.gpx")
    assert candidate["profile"] == "Alex" and candidate["date"] == "2026-11-01"
    assert candidate["time_s"] == pytest.approx(400, abs=1)   # finish counts within 5 m of the end
    trainer.handle({"cmd": "activity_start", "file": "col.gpx", "ghost": "Alex"})
    report = ride(trainer, 100)                        # Rider at 36 km/h: 1000 m in 100 s
    ghost = report["activity"]["ghost"]
    assert ghost["profile"] == "Alex" and ghost["distance_m"] == pytest.approx(500, abs=5)
    assert ghost["gap_m"] == pytest.approx(500, abs=5) and ghost["gap_s"] == pytest.approx(100, abs=2)
    report = ride(trainer, 101)
    finished = [e for e in report["events"] if e["type"] == "route_finished"][0]
    assert finished["time_s"] == pytest.approx(200, abs=1)
    assert finished["ghost_profile"] == "Alex" and finished["ghost_gap_s"] == pytest.approx(200, abs=1)
    trainer.handle({"cmd": "session_stop"})
    assert [g["profile"] for g in trainer.ghost_candidates("col.gpx")] == ["Rider", "Alex"]
    assert trainer.ghost_candidates("test.zwo") == []


def test_live_record_and_weekly_goal_events(trainer, tmp_path):  # noqa: F811
    monday = datetime.now() - timedelta(days=datetime.now().weekday())
    write_session(tmp_path / "sessions" / "Rider", "prev",
                  monday.replace(hour=0, minute=0, second=1, microsecond=0), [150] * 100)   # 1 km
    trainer.handle({"cmd": "setting", "key": "weekly_goal_km", "value": 2})
    trainer.handle({"cmd": "session_start"})           # FakeLink rides at 180 W, 10 m/s
    report = ride(trainer, 70)
    assert [(e["type"], e.get("window_s"), e.get("watts")) for e in report["events"]] == [
        ("record", 60, 180)]
    report = ride(trainer, 40)                         # 1 km before + 1.1 km now
    assert report["events"][-1] == {"id": 2, "type": "goal", "km": 2.0}
    trainer.handle({"cmd": "session_stop"})
    stats = trainer.stats()["Rider"]
    assert stats["best_power"]["60"] == 180 and stats["week_km"] == 2.1
    assert stats["streak_weeks"] == 1


def test_outdoor_rides_count_for_distance_not_for_power(trainer, tmp_path):  # noqa: F811
    from bushido.history import all_rides, load_outdoor
    sessions = tmp_path / "sessions"
    monday = date.today() - timedelta(days=date.today().weekday())
    first = trainer.add_outdoor_ride(monday, km=62.5, minutes=150, ascent_m=800, note=" Vallée de l'Ourthe ")
    second = trainer.add_outdoor_ride(monday - timedelta(days=7), 40, 95, 300, "")
    assert (first["id"], second["id"]) == (1, 2) and first["note"] == "Vallée de l'Ourthe"
    stats = trainer.stats()["Rider"]
    assert stats["week_km"] == 62.5 and stats["total_km"] == 102.5 and stats["longest_km"] == 62.5
    assert stats["best_power"] == {"60": None, "300": None, "1200": None}
    assert [r["outdoor"] for r in all_rides(sessions, "Rider")] == [True, True]
    assert trainer.delete_outdoor_ride(2) and not trainer.delete_outdoor_ride(2)
    assert [r["id"] for r in load_outdoor(sessions, "Rider")] == [1]


def test_progress_page_data(trainer, tmp_path):  # noqa: F811
    today = date(2026, 11, 19)                                    # a Thursday
    monday = datetime(2026, 11, 16, 18)
    write_session(tmp_path / "sessions" / "Rider", "a", monday, [200] * 1500)             # 15 km
    write_session(tmp_path / "sessions" / "Rider", "b", monday - timedelta(weeks=3), [180] * 600)
    trainer.add_outdoor_ride(date(2026, 11, 17), 20, 50, 250, "")
    trainer.handle({"cmd": "setting", "key": "weekly_goal_km", "value": 60})
    trainer.handle({"cmd": "setting", "key": "ftp_w", "value": 210})
    trainer.handle({"cmd": "event_set", "name": "Étape du Tour", "date": "2027-07-11"})
    progress = trainer.progress(today)
    assert progress["week"] == {"km": 35.0, "goal_km": 60, "remaining_km": 25.0, "days_left": 4}
    assert progress["event"] == {"name": "Étape du Tour", "date": "2027-07-11", "days_left": 234}
    weeks = progress["weeks"]
    assert len(weeks) == 12 and weeks[-1]["start"] == "2026-11-16"
    assert (weeks[-1]["indoor_km"], weeks[-1]["outdoor_km"], weeks[-1]["ascent_m"]) == (15.0, 20.0, 250)
    assert weeks[-1]["best_300_w"] == 200 and weeks[-1]["best_1200_w"] == 200
    assert weeks[-4]["indoor_km"] == 6.0 and weeks[-4]["best_1200_w"] is None
    assert weeks[-2]["indoor_km"] == 0 and weeks[-2]["best_300_w"] is None
    assert progress["ftp_history"][-1]["ftp_w"] == 210
    assert [r.get("outdoor", False) for r in progress["rides"]] == [True, False, False]   # newest first


def test_event_and_ftp_history_survive_a_restart(trainer, tmp_path):  # noqa: F811
    from bushido.library import ProfileStore
    trainer.handle({"cmd": "event_set", "name": "Étape du Tour", "date": "2027-07-11"})
    trainer.handle({"cmd": "setting", "key": "ftp_w", "value": 215})
    trainer.handle({"cmd": "setting", "key": "ftp_w", "value": 220})                     # same day
    store = ProfileStore(tmp_path / "profiles.json")
    assert store.events["Rider"] == {"name": "Étape du Tour", "date": "2027-07-11"}
    assert [h["ftp_w"] for h in store.ftp_history["Rider"]] == [220]
    with pytest.raises(ValueError):
        trainer.handle({"cmd": "event_set", "name": "X", "date": "bientôt"})
    trainer.handle({"cmd": "event_set", "name": "", "date": ""})
    assert trainer.progress()["event"] is None
