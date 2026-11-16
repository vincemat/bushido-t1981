"""Radio, protocol and physics constants for the Tacx Bushido T1981 brake.

Radio parameters and page layouts: switchabl's "Tacx legacy ANT protocol
description" (CC BY 4.0) and the FortiusANT `bushido` branch, confirmed on
a real brake in November 2026 (see README).
"""

import time

# Changes at every server start: a page loaded from an older server reloads itself.
APP_VERSION = str(int(time.time()))

# --- ANT channel (PC = slave, brake = master) -------------------------------
NETWORK_NUMBER = 0x00
PUBLIC_NETWORK_KEY = [0x00] * 8  # ANT public network, not the ANT+ key
RF_CHANNEL = 60                  # 2400 + 60 = 2460 MHz
DEVICE_TYPE = 81                 # 0x51, Bushido brake (the T1982 head unit is 82)
DEVICE_NUMBER_ANY = 0
TRANSMISSION_TYPE_ANY = 0
CHANNEL_PERIOD = 4096            # 32768 / 4096 = 8 Hz
SEARCH_TIMEOUT_INFINITE = 255

# --- Data pages --------------------------------------------------------------
PAGE_FORCE_POWER = 0x01          # brake -> PC, and PC -> brake (target force)
PAGE_SPEED_CADENCE = 0x02
PAGE_DISTANCE = 0x08
PAGE_ALARM = 0x10
PAGE_CALIBRATION = 0x22          # brake -> PC: calibration status
PAGE_CALIBRATION_COMMAND = 0x23  # PC -> brake
FORCE_UNITS_PER_NEWTON = 10      # forces travel in 0.1 N
SPEED_UNITS_PER_KMH = 10         # speeds travel in 0.1 km/h
MAX_FORCE_UNITS = 0xFFFF

# --- Link timing -------------------------------------------------------------
CONTROL_HZ = 4                   # target force is re-sent every 0.25 s
BROADCAST_EVERY_TICKS = 2        # UI refresh = CONTROL_HZ / 2 = 2 Hz
STALE_AFTER_S = 3.0              # no page for this long = brake asleep
DONGLE_RETRY_S = 3.0
SHUTDOWN_ZERO_REPEATS = 4        # the brake keeps its last target: clear it

# --- Physics -----------------------------------------------------------------
GRAVITY = 9.81
KMH_PER_MS = 3.6
DEFAULT_MASS_KG = 80.0           # rider + bike (typical 72 kg rider + 8 kg road bike)
DEFAULT_INERTIA_KG = 5.0         # extra virtual mass (experimental, see physics.py)
ROLLING_COEFF = 0.004            # gist: presumed by the original software
# 0.5 * air density 1.226 * frontal area 0.5 * drag coeff 0.5 (TTS4 defaults)
AERO_COEFF = 0.153               # N per (m/s)^2

MAX_FORCE_N = 60.0
MAX_FORCE_RATE_N_PER_S = 15.0    # smooth target changes
# The self-powered brake could not hold 30 N below ~10 km/h (test 2026-11-15)
# and a high force at low speed makes pedalling jerky: fade the target out.
LOW_SPEED_ZERO_KMH = 4.0
LOW_SPEED_FULL_KMH = 10.0
ERG_MIN_SPEED_MS = 3.0           # F = P / v would explode at low speed
ACCEL_FILTER_ALPHA = 0.5
MAX_ACCEL_MS2 = 1.5

SLOPE_MIN_PCT, SLOPE_MAX_PCT = -5.0, 15.0
ERG_MIN_W, ERG_MAX_W = 0.0, 600.0
MASS_MIN_KG, MASS_MAX_KG = 40.0, 150.0
INERTIA_MIN_KG, INERTIA_MAX_KG = 0.0, 30.0

MOVING_MIN_KMH = 1.0             # session time only counts above this speed

# --- Workouts and routes -----------------------------------------------------
DEFAULT_FTP_W = 200.0
FTP_MIN_W, FTP_MAX_W = 80.0, 500.0
DEFAULT_DIFFICULTY_PCT = 100.0   # share of a route's gradient that is simulated
DIFFICULTY_MIN_PCT, DIFFICULTY_MAX_PCT = 0.0, 100.0
EARTH_RADIUS_M = 6_371_000.0
ROUTE_SMOOTH_WINDOW_M = 100.0    # GPS elevation is noisy: average it before deriving a slope
ROUTE_SLOPE_WINDOW_M = 60.0
ROUTE_MIN_POINT_SPACING_M = 1.0  # drop GPS points closer than this (stops, jitter)
FREE_RIDE_SLOPE_PCT = 1.0        # road simulated during free-ride steps (rider adjusts it)
FTP_TEST_FACTOR = 0.95           # FTP ~ 95 % of the best 20-minute average power
FTP_TEST_MIN_COMPLETION = 0.95   # share of the test step that must be ridden for a result
PROFILE_POINTS = 400             # points sent to the page to draw a profile
ODOMETER_SAVE_EVERY_S = 60.0     # the lifetime odometer is written at most this often

# --- Records, goals and ghosts -----------------------------------------------
RECORD_WINDOWS_S = (60, 300, 1200)   # best average power over 1, 5 and 20 minutes
DEFAULT_WEEKLY_GOAL_KM = 0.0         # 0 = no goal
WEEKLY_GOAL_MAX_KM = 1000.0
GHOST_FINISH_TOLERANCE_M = 5.0       # a route counts as finished within this of its end
EVENTS_KEPT = 10                     # recent events (records, goal) kept in the report
PROGRESS_WEEKS = 12              # weeks shown on the progress page
RECENT_RIDES = 15
MIN_SESSION_S = 10.0             # shorter sessions are discarded, not saved
