"""Spin-down calibration of the T1981, driven through pages 0x23 (PC) / 0x22 (brake).

The sequence follows rjon65's BushidoBridge.py, the only routine reported to
work against a real brake without the head unit; status names come from
switchabl's protocol description. Page 0x22 carries a sub-index in byte 1
(3 = idle/result, 6 = procedure running) and a status in byte 3.

The brake measures the tyre/roller drag while the wheel coasts down from
40 km/h. The result is a force; 13 to 19 N is the range the head unit accepted.
"""

from enum import Enum

# Commands, byte 1 of page 0x23
CMD_START = 0x63
CMD_CONTINUE = 0x00
CMD_REQUEST_STATUS = 0x58
CMD_REQUEST_VALUE = 0x4D

INDEX_RESULT = 3
INDEX_RUNNING = 6

# Statuses, byte 3 of page 0x22
RESULT_IDLE = 0x00
RESULT_FINISHED = 0x0C
RESULT_ERROR = 0x0D
RESULT_VALUE = 0x42
RESULT_VALUE_READY = 0x4D
RUNNING_REQUESTED = 0x00
RUNNING_SPEED_UP = (0x01, 0x02)
RUNNING_STOP_PEDALLING = (0x03, 0x04)
RUNNING_COASTING = 0x05
RUNNING_PROCESSING = 0x06
RUNNING_COAST_DONE = (0x0A, 0x0B)
# rjon65 keeps sending CMD_CONTINUE while the brake reports these
RUNNING_NEEDS_CONTINUE = (0x01, 0x02, 0x03)

VALUE_MIN_N, VALUE_MAX_N = 13.0, 19.0


class Phase(str, Enum):
    STARTING = "starting"
    SPEED_UP = "speed_up"
    STOP_PEDALLING = "stop_pedalling"
    COASTING = "coasting"
    PROCESSING = "processing"
    RESUME = "resume"          # coast-down over: pedal again so the brake can answer
    READING = "reading"
    DONE = "done"
    ERROR = "error"


FINAL_PHASES = (Phase.DONE, Phase.ERROR)


class Calibrator:
    """Follows the brake's calibration status and says which command to send next."""

    def __init__(self) -> None:
        self.phase = Phase.STARTING
        self.value_n: float | None = None

    @property
    def finished(self) -> bool:
        return self.phase in FINAL_PHASES

    def update(self, index: int | None, status: int | None, value_n: float | None) -> int | None:
        """Take the latest page 0x22 fields; return the command to send, or None."""
        if self.finished or index is None or status is None:
            return None
        if index == INDEX_RESULT:
            if status == RESULT_IDLE:
                if self.phase is Phase.STARTING:
                    return CMD_START
                # Back to idle mid-procedure: the brake dropped the calibration
                # (e.g. it lost power when the wheel stopped). Let the user retry.
                self.phase = Phase.ERROR
                return None
            if status == RESULT_FINISHED:
                self.phase = Phase.READING
                return CMD_REQUEST_STATUS
            if status == RESULT_VALUE_READY:
                self.phase = Phase.READING
                return CMD_REQUEST_VALUE
            if status == RESULT_VALUE:
                self.phase, self.value_n = Phase.DONE, value_n
            elif status == RESULT_ERROR:
                self.phase = Phase.ERROR
            return None
        if index == INDEX_RUNNING:
            if status in RUNNING_SPEED_UP:
                self.phase = Phase.SPEED_UP
            elif status in RUNNING_STOP_PEDALLING:
                self.phase = Phase.STOP_PEDALLING
            elif status == RUNNING_COASTING:
                self.phase = Phase.COASTING
            elif status == RUNNING_PROCESSING:
                self.phase = Phase.PROCESSING
            elif status in RUNNING_COAST_DONE:
                self.phase = Phase.RESUME
            return CMD_CONTINUE if status in RUNNING_NEEDS_CONTINUE else None
        return None
