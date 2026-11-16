import pytest

from bushido import constants as c
from bushido.physics import Controller, Mode, Settings, erg_force, low_speed_factor, road_force

KMH = 1 / c.KMH_PER_MS


def settle(ctl: Controller, speed_ms: float, seconds: float = 20.0) -> float:
    force = 0.0
    for _ in range(int(seconds * c.CONTROL_HZ)):
        force = ctl.step(speed_ms, 1 / c.CONTROL_HZ)
    return force


def test_road_force_on_the_flat():
    # 85 kg at 30 km/h: rolling 3.3 N + aero 10.6 N
    assert road_force(30 * KMH, 0.0, 85.0) == pytest.approx(13.96, abs=0.05)


def test_road_force_adds_gravity_on_a_climb():
    flat = road_force(20 * KMH, 0.0, 85.0)
    assert road_force(20 * KMH, 5.0, 85.0) - flat == pytest.approx(85 * 9.81 * 0.05)


def test_erg_force_is_power_over_speed_with_a_floor():
    assert erg_force(5.0, 200.0) == pytest.approx(40.0)
    assert erg_force(0.5, 200.0) == pytest.approx(200.0 / c.ERG_MIN_SPEED_MS)


def test_low_speed_factor_fades_the_force_out():
    assert low_speed_factor(c.LOW_SPEED_ZERO_KMH * KMH) == 0.0
    assert low_speed_factor(c.LOW_SPEED_FULL_KMH * KMH) == pytest.approx(1.0)
    assert low_speed_factor(7 * KMH) == pytest.approx(0.5)


def test_free_mode_sends_zero():
    assert settle(Controller(), 25 * KMH) == 0.0


def test_slope_mode_converges_to_road_force():
    ctl = Controller(Settings(mass_kg=85.0, inertia_kg=0.0))
    ctl.mode, ctl.slope_pct = Mode.SLOPE, 2.0
    assert settle(ctl, 20 * KMH) == pytest.approx(road_force(20 * KMH, 2.0, 85.0))


def test_erg_mode_holds_power():
    ctl = Controller(Settings(inertia_kg=0.0))
    ctl.mode, ctl.erg_w = Mode.ERG, 150.0
    speed = 22 * KMH
    assert settle(ctl, speed) * speed == pytest.approx(150.0)


def test_target_changes_are_rate_limited():
    ctl = Controller()
    ctl.mode, ctl.force_n = Mode.FORCE, 40.0
    assert ctl.step(20 * KMH, 0.25) == pytest.approx(c.MAX_FORCE_RATE_N_PER_S * 0.25)


def test_force_is_capped():
    ctl = Controller(Settings(mass_kg=150.0, inertia_kg=0.0))
    ctl.mode, ctl.slope_pct = Mode.SLOPE, 15.0
    assert settle(ctl, 30 * KMH) == c.MAX_FORCE_N


def test_no_force_when_almost_stopped():
    ctl = Controller(Settings(inertia_kg=0.0))
    ctl.mode, ctl.erg_w = Mode.ERG, 300.0
    assert settle(ctl, 3 * KMH) == 0.0


def test_inertia_eases_off_while_slowing_down():
    steady = Controller(Settings(inertia_kg=10.0))
    slowing = Controller(Settings(inertia_kg=10.0))
    for ctl in (steady, slowing):
        ctl.mode, ctl.slope_pct = Mode.SLOPE, 3.0
    steady.observe_speed(0.0, 6.0)
    steady.observe_speed(1.0, 6.0)
    slowing.observe_speed(0.0, 6.5)
    slowing.observe_speed(1.0, 6.0)
    assert settle(slowing, 6.0) < settle(steady, 6.0)


def test_reset_clears_target_and_acceleration():
    ctl = Controller()
    ctl.mode, ctl.force_n = Mode.FORCE, 20.0
    settle(ctl, 20 * KMH)
    ctl.reset()
    assert ctl.target_n == 0.0
