"""Links to the brake: the real ANT one and a simulated one for UI work."""

import copy
import logging
import math
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

from . import constants as c
from . import calibration as cal
from .protocol import BrakeData, decode_page

logger = logging.getLogger(__name__)

STATUS_NO_DONGLE = "no_dongle"
STATUS_SEARCHING = "searching"   # dongle ready, brake asleep (nobody pedals)
STATUS_CONNECTED = "connected"


@dataclass
class LinkState:
    status: str = STATUS_SEARCHING
    data: BrakeData = field(default_factory=BrakeData)
    speed_ts: float = 0.0        # time of the last speed page, for acceleration


class BrakeLink(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def snapshot(self) -> LinkState: ...
    def send_page(self, page: list[int]) -> None: ...


class _BaseLink:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data = BrakeData()
        self._last_page_ts = 0.0
        self._speed_ts = 0.0
        self._dongle_ok = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=type(self).__name__, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        raise NotImplementedError

    def _on_page(self, page: bytes) -> None:
        now = time.monotonic()
        with self._lock:
            number = decode_page(page, self._data)
            self._last_page_ts = now
            if number == c.PAGE_SPEED_CADENCE:
                self._speed_ts = now

    def snapshot(self) -> LinkState:
        with self._lock:
            if not self._dongle_ok:
                return LinkState(status=STATUS_NO_DONGLE)
            if time.monotonic() - self._last_page_ts > c.STALE_AFTER_S:
                # The brake is self-powered: no page means the wheel stopped.
                return LinkState(status=STATUS_SEARCHING)
            return LinkState(STATUS_CONNECTED, copy.copy(self._data), self._speed_ts)


class AntLink(_BaseLink):
    """Slave ANT channel on a 0fcf:1008/1009 USB stick, through openant."""

    def __init__(self) -> None:
        super().__init__()
        self._node = None
        self._channel = None

    def _run(self) -> None:
        # Imported here so the simulator and the tests do not need a USB backend.
        from openant.base.driver import DriverNotFound
        from openant.easy.channel import Channel
        from openant.easy.node import Node
        from usb.core import USBError

        while not self._stop.is_set():
            try:
                node = Node()
            except (DriverNotFound, USBError) as exc:
                with self._lock:
                    self._dongle_ok = False
                logger.info("No usable ANT stick (%s), retrying", type(exc).__name__)
                self._stop.wait(c.DONGLE_RETRY_S)
                continue
            with self._lock:
                self._dongle_ok = True
            try:
                node.set_network_key(c.NETWORK_NUMBER, c.PUBLIC_NETWORK_KEY)
                channel = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE, c.NETWORK_NUMBER)
                channel.set_id(c.DEVICE_NUMBER_ANY, c.DEVICE_TYPE, c.TRANSMISSION_TYPE_ANY)
                channel.set_rf_freq(c.RF_CHANNEL)
                channel.set_period(c.CHANNEL_PERIOD)
                channel.set_search_timeout(c.SEARCH_TIMEOUT_INFINITE)
                channel.on_broadcast_data = lambda data: self._on_page(bytes(data[:8]))
                channel.on_acknowledge_data = channel.on_broadcast_data
                channel.open()
                self._node, self._channel = node, channel
                logger.info("ANT channel open on 2460 MHz, waiting for the brake")
                node.start()  # blocks until node.stop()
            finally:
                self._channel = None
                self._node = None
                node.stop()

    def send_page(self, page: list[int]) -> None:
        channel = self._channel
        if channel is not None:
            channel.send_broadcast_data(page)

    def stop(self) -> None:
        self._stop.set()
        node = self._node
        if node is not None:
            node.stop()
        self._thread.join(timeout=5)


class SimulatedLink(_BaseLink):
    """Fake brake and rider, to exercise the app without pedalling.

    A rider who holds a cruising speed (with a pedalling ripple) drives a light
    flywheel against the commanded force. Pages go through the real
    encoder/decoder path at the real rates. Calibration follows the sequence
    documented in calibration.py, with a rider who obeys the instructions.
    """

    CRUISE_SPEED_MS = 27 / c.KMH_PER_MS
    RIDER_GAIN_N_PER_MS = 25.0        # extra push per m/s below the speed the rider wants
    FLYWHEEL_KG = 12.0
    TYRE_DRAG_N = 15.0
    CADENCE_RPM = 85
    TICK_S = 1 / 8
    CAL_TOP_SPEED_MS = 40 / c.KMH_PER_MS
    CAL_END_SPEED_MS = 15 / c.KMH_PER_MS

    def __init__(self) -> None:
        super().__init__()
        self._target_n = 0.0
        self._cal = (cal.INDEX_RESULT, cal.RESULT_IDLE)

    def send_page(self, page: list[int]) -> None:
        if page[0] == c.PAGE_FORCE_POWER:
            self._target_n = struct.unpack(">H", bytes(page[1:3]))[0] / c.FORCE_UNITS_PER_NEWTON
        elif page[0] == c.PAGE_CALIBRATION_COMMAND:
            command = page[1]
            if command == cal.CMD_START and self._cal == (cal.INDEX_RESULT, cal.RESULT_IDLE):
                self._cal = (cal.INDEX_RUNNING, cal.RUNNING_SPEED_UP[1])
            elif command == cal.CMD_REQUEST_STATUS and self._cal[1] == cal.RESULT_FINISHED:
                self._cal = (cal.INDEX_RESULT, cal.RESULT_VALUE_READY)
            elif command == cal.CMD_REQUEST_VALUE and self._cal[1] == cal.RESULT_VALUE_READY:
                self._cal = (cal.INDEX_RESULT, cal.RESULT_VALUE)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _calibration_step(self, speed: float) -> bool:
        """Advance the fake calibration; return whether the rider is pedalling."""
        index, status = self._cal
        if index != cal.INDEX_RUNNING:
            return True
        if status in cal.RUNNING_SPEED_UP and speed >= self.CAL_TOP_SPEED_MS:
            self._cal = (index, cal.RUNNING_STOP_PEDALLING[1])
        elif status in cal.RUNNING_STOP_PEDALLING:
            self._cal = (index, cal.RUNNING_COASTING)
        elif status == cal.RUNNING_COASTING and speed <= self.CAL_END_SPEED_MS:
            self._cal = (cal.INDEX_RESULT, cal.RESULT_FINISHED)
        return self._cal[1] in cal.RUNNING_SPEED_UP

    def _run(self) -> None:
        speed, distance, t, tick = 2.0, 0.0, 0.0, 0
        while not self._stop.wait(self.TICK_S):
            pedalling = self._calibration_step(speed)
            calibrating = self._cal[0] == cal.INDEX_RUNNING
            ripple = 1 + 0.1 * math.sin(2 * math.pi * 2 * self.CADENCE_RPM / 60 * t)
            brake_n = (self.TYRE_DRAG_N if calibrating else max(self._target_n, self.TYRE_DRAG_N))
            # The rider holds a cruising speed: they push as hard as the brake resists,
            # so their power follows the target, as a real rider's does.
            wanted = self.CAL_TOP_SPEED_MS * 1.1 if calibrating else self.CRUISE_SPEED_MS
            pedal_n = max(brake_n + self.RIDER_GAIN_N_PER_MS * (wanted - speed), 0.0) * ripple
            speed = max(speed + (pedal_n * pedalling - brake_n) / self.FLYWHEEL_KG * self.TICK_S, 0.0)
            distance += speed * self.TICK_S
            t += self.TICK_S
            tick += 1
            force_units = round(brake_n * c.FORCE_UNITS_PER_NEWTON)
            value = round(self.TYRE_DRAG_N * c.FORCE_UNITS_PER_NEWTON) \
                if self._cal[1] == cal.RESULT_VALUE else 0
            pages = [
                struct.pack(">BHH3x", c.PAGE_FORCE_POWER, force_units, round(pedal_n * pedalling * speed)),
                struct.pack(">BHB4x", c.PAGE_SPEED_CADENCE,
                            round(speed * c.KMH_PER_MS * c.SPEED_UNITS_PER_KMH), self.CADENCE_RPM),
                struct.pack(">BI3x", c.PAGE_DISTANCE, round(distance)),
                struct.pack(">BHxB3x", c.PAGE_ALARM, 0, 20),
                struct.pack(">BBxBH2x", c.PAGE_CALIBRATION, self._cal[0], self._cal[1], value),
            ]
            self._on_page(pages[tick % len(pages)])
