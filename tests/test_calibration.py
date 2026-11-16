"""The calibrator is walked through the sequence of rjon65's BushidoBridge.py."""

from bushido import calibration as cal
from bushido.calibration import Calibrator, Phase
from bushido.link import STATUS_CONNECTED, LinkState
from bushido.protocol import BrakeData, decode_page, encode_calibration_command
from bushido.trainer import Trainer


def test_page_22_from_the_real_brake_is_idle():
    data = BrakeData()
    decode_page(bytes.fromhex("2203000000000000"), data)
    assert (data.calibration_index, data.calibration_status) == (cal.INDEX_RESULT, cal.RESULT_IDLE)


def test_page_22_value():
    data = BrakeData()
    decode_page(bytes.fromhex("2203004200960000"), data)
    assert data.calibration_status == cal.RESULT_VALUE
    assert data.calibration_value_n == 15.0


def test_command_page():
    assert encode_calibration_command(cal.CMD_START) == [0x23, 0x63, 0, 0, 0, 0, 0, 0]


def test_full_sequence():
    c = Calibrator()
    steps = [
        ((3, 0x00), cal.CMD_START, Phase.STARTING),
        ((6, 0x00), None, Phase.STARTING),
        ((6, 0x02), cal.CMD_CONTINUE, Phase.SPEED_UP),
        ((6, 0x04), None, Phase.STOP_PEDALLING),
        ((6, 0x05), None, Phase.COASTING),
        ((6, 0x06), None, Phase.PROCESSING),
        ((6, 0x0B), None, Phase.RESUME),
        ((3, 0x0C), cal.CMD_REQUEST_STATUS, Phase.READING),
        ((3, 0x4D), cal.CMD_REQUEST_VALUE, Phase.READING),
    ]
    for (index, status), command, phase in steps:
        assert c.update(index, status, 0.0) == command
        assert c.phase is phase
    assert c.update(3, 0x42, 15.5) is None
    assert c.phase is Phase.DONE and c.value_n == 15.5
    assert c.update(3, 0x00, 0.0) is None      # finished: nothing more is sent


def test_brake_error():
    c = Calibrator()
    c.update(6, 0x02, 0.0)
    c.update(3, 0x0D, 0.0)
    assert c.phase is Phase.ERROR


def test_falling_back_to_idle_mid_procedure_is_an_error():
    c = Calibrator()
    c.update(6, 0x05, 0.0)
    assert c.update(3, 0x00, 0.0) is None
    assert c.phase is Phase.ERROR


def test_unknown_or_missing_status_changes_nothing():
    c = Calibrator()
    assert c.update(None, None, None) is None
    assert c.update(9, 0x77, 0.0) is None
    assert c.phase is Phase.STARTING


class FakeLink:
    def __init__(self):
        self.sent = []
        self.data = BrakeData(speed_kmh=30.0, power_w=100, calibration_index=3,
                              calibration_status=0, calibration_raw="2203000000000000")

    def snapshot(self): return LinkState(STATUS_CONNECTED, self.data, 1.0)
    def send_page(self, page): self.sent.append(page)


def test_trainer_sends_commands_instead_of_force_and_logs(tmp_path):
    trainer = Trainer(FakeLink(), tmp_path)
    trainer.handle({"cmd": "calibration_start"})
    report = trainer.tick(0.25)
    assert trainer.link.sent == [encode_calibration_command(cal.CMD_START)]
    assert report["calibration"]["phase"] == "starting"
    trainer.link.data.calibration_status = cal.RESULT_VALUE
    trainer.link.data.calibration_value_n = 16.0
    trainer.link.data.calibration_raw = "22030000420000a0"
    report = trainer.tick(0.25)
    calibration = report["calibration"]
    assert (calibration["phase"], calibration["value_n"]) == ("done", 16.0)
    assert calibration["last"]["value_n"] == 16.0      # remembered for the home screen
    trainer.tick(0.25)                           # force control is back
    assert trainer.link.sent[-1][0] == 0x01
    log = next(tmp_path.glob("calibration_*.csv")).read_text().splitlines()
    assert len(log) == 3


def test_cancel(tmp_path):
    trainer = Trainer(FakeLink(), tmp_path)
    trainer.handle({"cmd": "calibration_start"})
    trainer.handle({"cmd": "calibration_cancel"})
    assert trainer.tick(0.25)["calibration"]["phase"] is None
