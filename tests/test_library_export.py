import xml.etree.ElementTree as ET

import pytest

from bushido.export import EXT_NS, TCX_NS, csv_to_tcx
from bushido.library import Library, ProfileStore
from bushido.link import STATUS_CONNECTED, LinkState
from bushido.physics import Mode, Settings
from bushido.protocol import BrakeData
from bushido.trainer import Trainer
from tests.test_route import gpx, straight_climb
from tests.test_workout import ZWO


class FakeLink:
    def __init__(self, speed_kmh=36.0):
        self.sent = []
        self.data = BrakeData(speed_kmh=speed_kmh, power_w=180, cadence_rpm=85, force_n=18.0)

    def snapshot(self): return LinkState(STATUS_CONNECTED, self.data, 1.0)
    def send_page(self, page): self.sent.append(page)


@pytest.fixture
def library(tmp_path):
    lib = Library(tmp_path / "workouts", tmp_path / "routes")
    lib.save("test.zwo", ZWO.encode())
    lib.save("col.gpx", gpx(straight_climb()).encode())
    return lib


@pytest.fixture
def trainer(tmp_path, library):
    return Trainer(FakeLink(), tmp_path / "sessions", library=library,
                   settings_path=tmp_path / "profiles.json")


def ride(trainer, seconds):
    for _ in range(int(seconds * 4)):
        report = trainer.tick(0.25)
    return report


def test_listing(library):
    listing = library.listing()
    assert listing["workouts"] == [{"file": "test.zwo", "name": "Test", "duration_s": 310,
                                    "description": "d", "ftp_test": False}]
    assert listing["routes"][0]["file"] == "col.gpx"


@pytest.mark.parametrize("name", ["../x.zwo", "a/b.gpx", "x.txt", ".zwo", "x.zwo/..", "a\\b.zwo"])
def test_unsafe_names_are_rejected(library, name):
    with pytest.raises(ValueError):
        library.save(name, ZWO.encode())


def test_unparsable_upload_is_rejected_and_not_stored(library):
    with pytest.raises(ValueError):
        library.save("bad.gpx", b"<gpx/>")
    assert not (library.routes_dir / "bad.gpx").exists()


def test_workout_drives_erg_targets_and_starts_a_session(trainer):
    trainer.handle({"cmd": "setting", "key": "ftp_w", "value": 250})
    trainer.handle({"cmd": "activity_start", "file": "test.zwo"})
    report = ride(trainer, 50)
    assert trainer.controller.mode is Mode.ERG
    assert trainer.controller.erg_w == pytest.approx(0.6 * 250, abs=1)   # mid-ramp 0.4 -> 0.8
    assert report["activity"]["label"] == "Échauffement"
    assert report["activity"]["next_label"] == "Intervalle 1/2"
    assert report["session"] is not None
    trainer.handle({"cmd": "activity_skip"})
    assert ride(trainer, 1)["activity"]["target_w"] == 300               # 1.2 x 250


def test_workout_end_is_announced_and_leaves_a_flat_road(trainer):
    trainer.handle({"cmd": "activity_start", "file": "test.zwo"})
    report = ride(trainer, 311)
    assert report["activity"] is None and report["finished_activity"] == "Test"
    assert report["events"][-1]["type"] == "workout_finished"
    assert (trainer.controller.mode, trainer.controller.slope_pct) == (Mode.SLOPE, 0.0)
    assert report["session"] is not None               # the rider decides when to stop
    trainer.handle({"cmd": "session_stop"})
    assert trainer.tick(0.25)["finished_activity"] is None


def test_no_progress_while_stopped(tmp_path, library):
    trainer = Trainer(FakeLink(speed_kmh=0.0), tmp_path, library=library)
    trainer.handle({"cmd": "activity_start", "file": "test.zwo"})
    assert ride(trainer, 30)["activity"]["elapsed_s"] == 0


def test_route_sets_the_slope_with_difficulty_and_logs_position(trainer):
    trainer.handle({"cmd": "setting", "key": "difficulty_pct", "value": 50})
    trainer.handle({"cmd": "activity_start", "file": "col.gpx"})
    report = ride(trainer, 100)                                          # 10 m/s -> 1 km
    assert report["activity"]["distance_m"] == pytest.approx(1000, abs=5)
    assert report["activity"]["slope_pct"] == pytest.approx(5.0, abs=0.1)
    assert trainer.controller.slope_pct == pytest.approx(2.5, abs=0.1)
    path = trainer.session.path
    trainer.handle({"cmd": "session_stop"})
    assert trainer.route is None                       # stopping the session ends the activity
    tcx = ET.parse(path.with_suffix(".tcx")).getroot()
    points = tcx.findall(f".//{{{TCX_NS}}}Trackpoint")
    assert len(points) == 100
    last = points[-1]
    assert float(last.find(f"{{{TCX_NS}}}Position/{{{TCX_NS}}}LatitudeDegrees").text) > 50.008
    assert float(last.find(f"{{{TCX_NS}}}AltitudeMeters").text) == pytest.approx(150, abs=2)
    assert last.find(f".//{{{EXT_NS}}}Watts").text == "180"
    assert tcx.find(f".//{{{TCX_NS}}}Id").text.endswith("Z")
    assert trainer.tick(0.25)["last_export"] == "Rider/" + path.with_suffix(".tcx").name


def test_empty_session_has_no_export(tmp_path):
    empty = tmp_path / "s.csv"
    empty.write_text("time,elapsed_s\n")
    assert csv_to_tcx(empty) is None


def test_profiles_round_trip_and_limits(tmp_path):
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    assert store.names() == ["Rider"] and store.current == Settings()
    store.create("Sam")
    store.current.ftp_w = 260
    store.add_distance(12_300)
    store.save()
    again = ProfileStore(path)
    assert again.active == "Sam" and again.names() == ["Rider", "Sam"]
    assert again.current.ftp_w == 260 and again.odometer_m["Sam"] == 12_300
    path.write_text('{"active": "Ghost", "profiles": {"A": {"ftp_w": 99999, "mass_kg": "x"}}}')
    odd = ProfileStore(path)
    assert odd.active == "A" and odd.current.ftp_w == 500
    assert odd.current.mass_kg == Settings().mass_kg
    path.write_text("not json")
    assert ProfileStore(path).names() == ["Rider"]


@pytest.mark.parametrize("name", ["", "a/b", "../x", "x" * 31, "rider"])
def test_bad_or_duplicate_profile_names(tmp_path, name):
    with pytest.raises(ValueError):
        ProfileStore(tmp_path / "p.json").create(name)


def test_each_profile_has_its_settings_distance_and_sessions(trainer, tmp_path):
    trainer.handle({"cmd": "setting", "key": "ftp_w", "value": 240})
    trainer.handle({"cmd": "session_start"})
    report = ride(trainer, 100)                                          # 1 km at 10 m/s
    assert report["trip_m"] == 1000 and report["profile"]["odometer_km"] == 1.0
    first_session = trainer.session.path
    trainer.handle({"cmd": "profile_create", "name": "Alex"})
    assert trainer.session is None and first_session.parent.name == "Rider"
    report = ride(trainer, 50)
    assert report["profile"] == {"active": "Alex", "names": ["Alex", "Rider"],
                                 "odometer_km": 0.5}
    assert report["trip_m"] == 500 and report["settings"]["ftp_w"] == Settings().ftp_w
    trainer.handle({"cmd": "session_start"})
    assert trainer.session.path.parent.name == "Alex"
    trainer.handle({"cmd": "profile_select", "name": "Rider"})
    report = trainer.tick(0.25)
    assert report["settings"]["ftp_w"] == 240
    assert report["profile"]["odometer_km"] == 1.0
    saved = ProfileStore(tmp_path / "profiles.json")
    assert saved.odometer_m == {"Rider": 1000, "Alex": 500}


def test_unknown_profile_is_rejected(trainer):
    with pytest.raises(ValueError):
        trainer.handle({"cmd": "profile_select", "name": "Nobody"})


FTP_TEST_ZWO = """<workout_file><name>FTP</name><workout>
  <SteadyState Duration="10" Power="0.5"/>
  <FreeRide Duration="100" ftptest="1"/>
  <SteadyState Duration="10" Power="0.5"/>
</workout></workout_file>"""


@pytest.fixture
def ftp_trainer(trainer, library):
    library.save("ftp.zwo", FTP_TEST_ZWO.encode())
    trainer.link.data.power_w = 240
    trainer.handle({"cmd": "activity_start", "file": "ftp.zwo"})
    return trainer


def test_ftp_test_step_simulates_a_road_the_rider_can_adjust(ftp_trainer):
    report = ride(ftp_trainer, 15)
    assert ftp_trainer.controller.mode is Mode.SLOPE
    assert ftp_trainer.controller.slope_pct == 1.0
    assert report["activity"]["ftp_test"] and report["activity"]["target_w"] is None
    ftp_trainer.handle({"cmd": "slope", "value": 3})
    ride(ftp_trainer, 5)
    assert ftp_trainer.controller.slope_pct == 3.0        # not overwritten during the step


def test_ftp_test_result_and_apply(ftp_trainer):
    report = ride(ftp_trainer, 60)
    assert report["activity"]["test_average_w"] == 240 and report["ftp_result"] is None
    report = ride(ftp_trainer, 55)                        # past the end of the test step
    assert report["ftp_result"] == {"average_w": 240, "ftp_w": 228}
    assert ftp_trainer.controller.mode is Mode.ERG
    ftp_trainer.handle({"cmd": "setting", "key": "ftp_w", "value": 228})
    ftp_trainer.handle({"cmd": "ftp_result_clear"})
    report = ftp_trainer.tick(0.25)
    assert report["settings"]["ftp_w"] == 228 and report["ftp_result"] is None


def test_skipped_ftp_test_gives_no_result(ftp_trainer):
    ride(ftp_trainer, 40)
    ftp_trainer.handle({"cmd": "activity_skip"})
    assert ride(ftp_trainer, 2)["ftp_result"] is None


def test_plain_free_ride_step_is_not_a_test(trainer, library):
    trainer.handle({"cmd": "activity_start", "file": "test.zwo"})
    report = ride(trainer, 215)                           # inside the FreeRide of ZWO
    assert report["activity"]["label"] == "Libre" and not report["activity"]["ftp_test"]
    assert trainer.controller.mode is Mode.SLOPE
    assert ride(trainer, 60)["ftp_result"] is None


def test_summary_after_a_session_and_short_sessions_are_discarded(trainer):
    trainer.handle({"cmd": "activity_start", "file": "test.zwo"})
    ride(trainer, 30)
    path = trainer.session.path
    trainer.handle({"cmd": "session_stop"})
    summary = trainer.tick(0.25)["last_session"]
    assert summary["activity"] == "Test" and summary["elapsed_s"] == 30
    assert summary["distance_m"] == 300 and summary["avg_power_w"] == 180
    assert summary["export"].endswith(".tcx")
    trainer.handle({"cmd": "summary_close"})
    assert trainer.tick(0.25)["last_session"] is None

    trainer.handle({"cmd": "session_start"})
    ride(trainer, 3)
    short = trainer.session.path
    trainer.handle({"cmd": "session_stop"})
    assert path.exists() and not short.exists()
    assert trainer.tick(0.25)["last_session"] is None
