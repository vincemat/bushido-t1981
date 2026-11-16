"""Convert a session CSV into a TCX file that Strava, Garmin Connect etc. accept."""

import csv
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

TCX_NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
EXT_NS = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"
KMH_PER_MS = 3.6


def _utc(local_iso: str) -> str:
    return (datetime.fromisoformat(local_iso).astimezone(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"))


def csv_to_tcx(csv_path: Path) -> Path | None:
    """Write `<session>.tcx` next to the CSV. Returns None for an empty session."""
    with csv_path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None
    ET.register_namespace("", TCX_NS)
    ET.register_namespace("ax", EXT_NS)  # "ns3" is reserved by ElementTree
    tag = lambda name: f"{{{TCX_NS}}}{name}"
    ext = lambda name: f"{{{EXT_NS}}}{name}"

    root = ET.Element(tag("TrainingCenterDatabase"))
    activity = ET.SubElement(ET.SubElement(root, tag("Activities")), tag("Activity"),
                             Sport="Biking")
    start = _utc(rows[0]["time"])
    ET.SubElement(activity, tag("Id")).text = start
    lap = ET.SubElement(activity, tag("Lap"), StartTime=start)
    energy_j = sum(float(r["power_w"]) for r in rows)   # one row per second
    ET.SubElement(lap, tag("TotalTimeSeconds")).text = rows[-1]["elapsed_s"]
    ET.SubElement(lap, tag("DistanceMeters")).text = rows[-1]["distance_m"]
    # Riding is ~24 % efficient and 1 kcal = 4.184 kJ, so kcal burnt ~= kJ of work.
    ET.SubElement(lap, tag("Calories")).text = str(round(energy_j / 1000))
    ET.SubElement(lap, tag("Intensity")).text = "Active"
    ET.SubElement(lap, tag("TriggerMethod")).text = "Manual"
    track = ET.SubElement(lap, tag("Track"))
    for row in rows:
        point = ET.SubElement(track, tag("Trackpoint"))
        ET.SubElement(point, tag("Time")).text = _utc(row["time"])
        if row.get("latitude") and row.get("longitude"):
            position = ET.SubElement(point, tag("Position"))
            ET.SubElement(position, tag("LatitudeDegrees")).text = row["latitude"]
            ET.SubElement(position, tag("LongitudeDegrees")).text = row["longitude"]
        if row.get("altitude_m"):
            ET.SubElement(point, tag("AltitudeMeters")).text = row["altitude_m"]
        ET.SubElement(point, tag("DistanceMeters")).text = row["distance_m"]
        ET.SubElement(point, tag("Cadence")).text = row["cadence_rpm"]
        tpx = ET.SubElement(ET.SubElement(point, tag("Extensions")), ext("TPX"))
        ET.SubElement(tpx, ext("Speed")).text = f"{float(row['speed_kmh']) / KMH_PER_MS:.2f}"
        ET.SubElement(tpx, ext("Watts")).text = str(round(float(row["power_w"])))
    out = csv_path.with_suffix(".tcx")
    ET.ElementTree(root).write(out, encoding="UTF-8", xml_declaration=True)
    return out
