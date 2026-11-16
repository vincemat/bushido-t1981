"""Encode and decode T1981 data pages. Pure functions, multi-byte fields are big-endian."""

import struct
from dataclasses import dataclass

from . import constants as c


@dataclass
class BrakeData:
    """Latest values decoded from the brake. None until the page was seen."""

    power_w: int | None = None
    force_n: float | None = None
    speed_kmh: float | None = None
    cadence_rpm: int | None = None
    distance_m: int | None = None
    alarm: int | None = None
    temperature_c: int | None = None
    calibration_index: int | None = None
    calibration_status: int | None = None
    calibration_value_n: float | None = None
    calibration_raw: str | None = None


def decode_page(page: bytes, data: BrakeData) -> int:
    """Update `data` from one 8-byte page and return the page number."""
    if len(page) != 8:
        raise ValueError(f"ANT page must be 8 bytes, got {len(page)}")
    number = page[0]
    if number == c.PAGE_FORCE_POWER:
        # Bytes 1-2 are undocumented; measured as force in 0.1 N on the real
        # brake (force x speed matched the reported power).
        force, power = struct.unpack(">HH", page[1:5])
        data.force_n = force / c.FORCE_UNITS_PER_NEWTON
        data.power_w = power
    elif number == c.PAGE_SPEED_CADENCE:
        speed, cadence = struct.unpack(">HB", page[1:4])
        data.speed_kmh = speed / c.SPEED_UNITS_PER_KMH
        data.cadence_rpm = cadence
    elif number == c.PAGE_DISTANCE:
        data.distance_m = struct.unpack(">I", page[1:5])[0]
    elif number == c.PAGE_ALARM:
        # The public spec puts the temperature in byte 3; on the real brake it
        # is byte 4 (18 -> 29 degC over a 100 s effort, byte 3 stayed 0).
        data.alarm = struct.unpack(">H", page[1:3])[0]
        data.temperature_c = page[4]
    elif number == c.PAGE_CALIBRATION:
        # Byte 1 is a sub-index (rjon65's BushidoBridge), byte 3 the status,
        # bytes 4-5 the measured drag in 0.1 N once calibration succeeded.
        data.calibration_index = page[1]
        data.calibration_status = page[3]
        data.calibration_value_n = struct.unpack(">H", page[4:6])[0] / c.FORCE_UNITS_PER_NEWTON
        data.calibration_raw = page.hex()
    return number


def encode_target_force(force_n: float) -> list[int]:
    """Page 0x01 PC -> brake: target force, UInt16 in 0.1 N."""
    units = round(max(force_n, 0.0) * c.FORCE_UNITS_PER_NEWTON)
    return list(struct.pack(">BH5x", c.PAGE_FORCE_POWER, min(units, c.MAX_FORCE_UNITS)))


def encode_calibration_command(command: int) -> list[int]:
    """Page 0x23 PC -> brake: one calibration command byte."""
    return list(struct.pack(">BB6x", c.PAGE_CALIBRATION_COMMAND, command))
