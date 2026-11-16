import csv
from datetime import datetime

import pytest

from bushido.link import STATUS_CONNECTED, STATUS_SEARCHING, LinkState
from bushido.protocol import BrakeData, encode_calibration_command, encode_target_force
from bushido.session import CSV_COLUMNS, Sample, Session
from bushido.trainer import Trainer


class FakeLink:
    def __init__(self):
        self.state = LinkState(status=STATUS_SEARCHING)
        self.sent = []
        self.stopped = False

    def start(self): ...
    def stop(self): self.stopped = True
    def snapshot(self): return self.state
    def send_page(self, page): self.sent.append(page)

    def ride(self, speed_kmh, power_w, ts):
        self.state = LinkState(STATUS_CONNECTED,
                               BrakeData(power_w=power_w, speed_kmh=speed_kmh, cadence_rpm=80,
                                         force_n=10.0, temperature_c=20), ts)


@pytest.fixture
def trainer(tmp_path):
    return Trainer(FakeLink(), tmp_path)


def test_nothing_is_sent_while_the_brake_sleeps(trainer):
    report = trainer.tick(0.25)
    assert trainer.link.sent == []
    assert report["brake"]["status"] == STATUS_SEARCHING
    assert report["brake"]["speed_kmh"] == 0.0


def test_force_mode_reaches_the_brake(trainer):
    trainer.handle({"cmd": "mode", "mode": "force"})
    trainer.handle({"cmd": "force", "value": 12})
    trainer.link.ride(20.0, 100, 1.0)
    for _ in range(40):
        report = trainer.tick(0.25)
    assert trainer.link.sent[-1] == encode_target_force(12.0)
    assert report["control"]["target_n"] == 12.0


def test_values_are_clamped(trainer):
    trainer.handle({"cmd": "slope", "value": 99})
    trainer.handle({"cmd": "setting", "key": "mass_kg", "value": "1"})
    assert trainer.controller.slope_pct == 15.0
    assert trainer.controller.settings.mass_kg == 40.0


@pytest.mark.parametrize("message", [{"cmd": "nope"}, {"cmd": "slope"}, {"cmd": "mode", "mode": "x"},
                                     {"cmd": "erg", "value": "abc"}, "text", None,
                                     {"cmd": "setting", "key": "nope", "value": 1},
                                     {"cmd": "activity_start", "file": "x.zwo"}])
def test_bad_commands_raise_value_error(trainer, message):
    with pytest.raises(ValueError):
        trainer.handle(message)


def test_target_restarts_from_zero_after_the_brake_slept(trainer):
    trainer.handle({"cmd": "mode", "mode": "force"})
    trainer.handle({"cmd": "force", "value": 30})
    trainer.link.ride(20.0, 100, 1.0)
    for _ in range(40):
        trainer.tick(0.25)
    trainer.link.state = LinkState(status=STATUS_SEARCHING)
    trainer.tick(0.25)
    assert trainer.controller.target_n == 0.0


def test_session_totals_and_csv(trainer, tmp_path):
    trainer.handle({"cmd": "session_start"})
    trainer.link.ride(36.0, 200, 1.0)          # 10 m/s
    for _ in range(40):                         # 10 s
        report = trainer.tick(0.25)
    assert report["session"]["elapsed_s"] == 10
    assert report["session"]["distance_m"] == 100
    assert report["session"]["avg_power_w"] == 200
    assert report["session"]["energy_kj"] == 2.0
    path = trainer.session.path
    trainer.handle({"cmd": "session_stop"})
    rows = list(csv.reader(path.open()))
    assert rows[0] == CSV_COLUMNS
    assert len(rows) == 11
    assert trainer.session is None


def test_stopped_time_is_not_counted(tmp_path):
    session = Session(tmp_path, datetime(2026, 1, 1))
    still = Sample(0.0, 0, 0, 0.0, 0.0, "free", 0.0, 0.0, None)
    session.add(still, 5.0)
    assert session.elapsed_s == 0.0
    session.close()


def test_shutdown_releases_the_brake(trainer, monkeypatch):
    monkeypatch.setattr("bushido.trainer.time.sleep", lambda _: None)
    trainer.shutdown()
    assert trainer.link.sent == [encode_target_force(0.0)] * 4
    assert trainer.link.stopped
