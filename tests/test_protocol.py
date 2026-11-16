"""Decoding is checked against pages captured from the real T1981."""

import pytest

from bushido.protocol import BrakeData, decode_page, encode_target_force


def decode(hex_page: str) -> BrakeData:
    data = BrakeData()
    decode_page(bytes.fromhex(hex_page), data)
    return data


def test_force_power_page():
    data = decode("0100570028005700")
    assert data.force_n == 8.7
    assert data.power_w == 40


def test_speed_cadence_page():
    data = decode("0200a21f00000000")
    assert data.speed_kmh == 16.2
    assert data.cadence_rpm == 31


def test_distance_page():
    assert decode("08000000b5000000").distance_m == 181


def test_alarm_page_gives_temperature():
    data = decode("1000000012000000")
    assert data.alarm == 0
    assert data.temperature_c == 18


def test_calibration_page():
    assert decode("2203000000000000").calibration_status == 0


def test_unknown_page_is_ignored():
    assert decode("ad01000c00001033") == BrakeData()


def test_wrong_length_is_rejected():
    with pytest.raises(ValueError):
        decode_page(b"\x01\x02", BrakeData())


def test_encode_target_force_is_big_endian_tenths_of_newton():
    assert encode_target_force(30.0) == [0x01, 0x01, 0x2C, 0, 0, 0, 0, 0]


def test_encode_target_force_clamps():
    assert encode_target_force(-5.0)[1:3] == [0, 0]
    assert encode_target_force(1e9)[1:3] == [0xFF, 0xFF]
