#!/usr/bin/env python3
"""
First contact with a Tacx Bushido T1981 brake, without the T1982 head unit.

Channel parameters and page layouts come from:
  - switchabl, "Tacx legacy ANT protocol description" (CC BY 4.0)
    https://gist.github.com/switchabl/75b2619e2e3381f49425479d59523ead
  - switchabl/FortiusANT, branch "bushido", SlaveBSD_ChannelConfig() and
    clsTacxAntBushidoDirectTrainer (tested on a real T1981 in Feb 2021)
    https://github.com/switchabl/FortiusANT/tree/bushido

API: openant 1.3.4 (openant.easy.node.Node / openant.easy.channel.Channel).

Usage:
  python sniff_t1981.py                 # listen only (step 1)
  python sniff_t1981.py --force 300     # also send target force 30.0 N every 0.25 s (step 2)

Pedal BEFORE and DURING the run: the brake is powered by its generator and
only transmits while the flywheel turns.
"""
import argparse
import csv
import os
import struct
import threading
import time
from datetime import datetime

from openant.easy.channel import Channel
from openant.easy.node import Node

# --- T1981 channel configuration (PC side = slave, brake = master) ----------
NETWORK_NUMBER = 0x00          # dongle-side network slot we load the key into
PUBLIC_NETWORK_KEY = [0x00] * 8  # ANT public network key (NOT the ANT+ key)
RF_CHANNEL = 60                # 2400 + 60 = 2460 MHz
DEVICE_TYPE = 81               # 0x51, Bushido brake
DEVICE_NUMBER = 0              # 0 = wildcard, pair with any brake
TRANSMISSION_TYPE = 0          # 0 = wildcard (FortiusANT branch uses 0x01)
CHANNEL_PERIOD = 4096          # 32768 / 4096 = 8 Hz
SEARCH_TIMEOUT_INFINITE = 255

seen_pages = {}
state = {"power": None, "speed": None, "cadence": None, "distance": None,
         "force": None, "target": None}


def decode(page: bytes):
    """Decode brake -> PC pages. Multi-byte fields are BIG-endian."""
    p = page[0]
    if p == 0x01:   # bytes 1-2 = measured force (0.1 N, observed), 3-4 = power (W)
        state["force"], state["power"] = struct.unpack(">HH", page[1:5])
    elif p == 0x02:  # speed (0.1 km/h) / cadence / balance
        speed, cad, bal = struct.unpack(">HBB", page[1:5])
        state["speed"], state["cadence"] = speed / 10.0, cad
    elif p == 0x08:  # distance in m (UInt32, bytes 1-4)
        state["distance"] = struct.unpack(">I", page[1:5])[0]
    elif p == 0x10:  # alarm bitmask + temperature
        alarm, temp = struct.unpack(">HB", page[1:4])
        if alarm:
            print(f"  ALARM 0x{alarm:04x} temp={temp}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", type=int, default=None,
                    help="target force in 0.1 N units (e.g. 300 = 30 N). Omit to only listen.")
    ap.add_argument("--csv", default=f"t1981_{datetime.now():%Y%m%d_%H%M%S}.csv")
    ap.add_argument("--profile", default=None,
                    help="force steps starting when the brake is found, e.g. "
                         "'none:20,150:30,300:30,none:20' (force in 0.1 N : seconds; "
                         "none = send nothing). The run stops after the last step.")
    ap.add_argument("--seconds", type=float, default=60,
                    help="stop automatically after this many seconds (0 = run until Ctrl+C)")
    args = ap.parse_args()

    node = Node()  # raises DriverNotFound if no 0fcf:1008/1009 stick is found
    node.set_network_key(NETWORK_NUMBER, PUBLIC_NETWORK_KEY)

    ch = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE, NETWORK_NUMBER)
    ch.set_id(DEVICE_NUMBER, DEVICE_TYPE, TRANSMISSION_TYPE)
    ch.set_rf_freq(RF_CHANNEL)
    ch.set_period(CHANNEL_PERIOD)
    ch.set_search_timeout(SEARCH_TIMEOUT_INFINITE)

    out = open(args.csv, "w", newline="")
    writer = csv.writer(out)
    writer.writerow(["t", "page_hex", "raw_hex", "power_w", "speed_kmh", "cadence_rpm", "distance_m",
                     "force_meas_01N", "force_target_01N"])
    t0 = time.time()
    last_print = [0.0]

    def on_data(data):
        page = bytes(data[:8])
        if not seen_pages:
            print(f">>> BRAKE FOUND after {time.time() - t0:.1f} s (first page 0x{page[0]:02x}: {page.hex()})",
                  flush=True)
        seen_pages[page[0]] = seen_pages.get(page[0], 0) + 1
        decode(page)
        writer.writerow([f"{time.time() - t0:.3f}", f"{page[0]:02x}", page.hex(),
                         state["power"], state["speed"], state["cadence"], state["distance"],
                         state["force"], state["target"]])
        if time.time() - last_print[0] > 1.0:
            last_print[0] = time.time()
            pages = " ".join(f"{k:02x}:{v}" for k, v in sorted(seen_pages.items()))
            print(f"target={state['target']}  F={state['force']}  P={state['power']} W  "
                  f"v={state['speed']} km/h  cad={state['cadence']} rpm  d={state['distance']} m",
                  flush=True)

    ch.on_broadcast_data = on_data
    ch.on_acknowledge_data = on_data
    ch.open()
    print(f"Channel open: 2460 MHz, devtype 81, period 4096 (8 Hz). Logging to {args.csv}")
    print("Start pedalling now...")

    stop = threading.Event()
    steps = None
    if args.profile:
        steps = []
        for part in args.profile.split(","):
            f, d = part.split(":")
            steps.append((None if f.strip().lower() == "none" else int(f), float(d)))
    elif args.force is not None:
        steps = [(args.force, float("inf"))]

    if steps:
        def sender():
            while not seen_pages and not stop.is_set():   # wait for the brake
                time.sleep(0.1)
            for force, duration in steps:
                state["target"] = force
                print(f"--- step: target={force} for {duration:.0f} s", flush=True)
                end = time.time() + duration
                while time.time() < end and not stop.is_set():
                    if force is not None:
                        # page 0x01, UInt16 big-endian, 0.1 N
                        ch.send_broadcast_data(list(struct.pack(">BH5x", 0x01, force)))
                    time.sleep(0.25)
            state["target"] = None
            if not stop.is_set():
                print("--- profile finished, stopping.", flush=True)
                stop.set()
                node.stop()
        threading.Thread(target=sender, daemon=True).start()

    if args.seconds > 0:
        def auto_stop():
            if not stop.wait(args.seconds):
                print(f"\n{args.seconds:.0f} s elapsed, stopping.", flush=True)
                stop.set()
                node.stop()  # makes node.start() return
        threading.Thread(target=auto_stop, daemon=True).start()
        print(f"Auto-stop after {args.seconds:.0f} s.", flush=True)

    try:
        node.start()  # blocks, dispatches callbacks
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        node.stop()
        out.close()
        print("Pages seen:", {f"0x{k:02x}": v for k, v in sorted(seen_pages.items())})
        if not seen_pages:
            print("NOTHING RECEIVED from the brake (were you pedalling the whole time?)")
        print(f"CSV: {os.path.abspath(args.csv)}")


if __name__ == "__main__":
    main()
