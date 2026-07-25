"""Capture a clean, warm-up-free ESP-NOW RSSI calibration interval."""

import argparse
import csv
import json
import statistics
import time
from pathlib import Path

import serial

from collector import FrameParser


FIELDS = (
    "measured_distance_m",
    "device_id",
    "distance_source",
    "distance_valid",
    "ranging_rssi_raw_dbm",
    "ranging_rssi_filtered_dbm",
    "ranging_config_version",
    "distance_mm",
    "distance_extrapolated",
    "within_one_meter",
    "range_trend_name",
    "range_trend_delta_db",
    "context_packet_seq",
    "received_at_ns",
)


def distance_tag(distance_m: float) -> str:
    return f"{distance_m:g}".replace(".", "p")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture one fixed-distance, bidirectional RSSI interval."
    )
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--baudrate", type=int, default=2_000_000)
    parser.add_argument("--distance", type=float, required=True)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--warmup", type=float, default=3.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.distance <= 0 or args.duration <= 0 or args.warmup < 0:
        parser.error("distance/duration must be positive and warmup non-negative")

    output = args.output or Path("data") / (
        f"espnow_calibration_{distance_tag(args.distance)}m.csv"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    context_counts: dict[str, int] = {}
    valid_values: dict[str, list[int]] = {}
    frame_parser = FrameParser()

    with serial.Serial(args.port, args.baudrate, timeout=0.2) as connection:
        warmup_until = time.monotonic() + args.warmup
        while time.monotonic() < warmup_until:
            connection.read(connection.in_waiting or 1)

        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            capture_until = time.monotonic() + args.duration
            while time.monotonic() < capture_until:
                raw = connection.read(connection.in_waiting or 1)
                for packet in frame_parser.feed(raw):
                    if packet.get("type") != "sensor_context_packet":
                        continue
                    device_id = str(packet["device_id"])
                    context_counts[device_id] = context_counts.get(device_id, 0) + 1
                    rssi = packet.get("ranging_rssi_raw_dbm")
                    if rssi is not None:
                        valid_values.setdefault(device_id, []).append(int(rssi))
                    writer.writerow(
                        {
                            "measured_distance_m": f"{args.distance:g}",
                            "device_id": device_id,
                            "distance_source": packet["distance_source"],
                            "distance_valid": int(bool(packet["distance_valid"])),
                            "ranging_rssi_raw_dbm": rssi if rssi is not None else "",
                            "ranging_rssi_filtered_dbm": (
                                packet["ranging_rssi_filtered_dbm"]
                                if packet["ranging_rssi_filtered_dbm"] is not None
                                else ""
                            ),
                            "ranging_config_version": packet[
                                "ranging_config_version"
                            ],
                            "distance_mm": (
                                packet["distance_mm"]
                                if packet["distance_valid"]
                                else ""
                            ),
                            "distance_extrapolated": int(
                                bool(packet["distance_extrapolated"])
                            ),
                            "within_one_meter": int(
                                bool(packet["within_one_meter"])
                            ),
                            "range_trend_name": packet["range_trend_name"],
                            "range_trend_delta_db": (
                                packet["range_trend_delta_db"]
                                if packet["range_trend_delta_db"] is not None
                                else ""
                            ),
                            "context_packet_seq": packet["packet_seq"],
                            "received_at_ns": time.time_ns(),
                        }
                    )

    summary = {
        "distance_m": args.distance,
        "duration_s": args.duration,
        "output": str(output),
        "crc_errors": frame_parser.crc_errors,
        "devices": {},
    }
    for device_id in sorted(context_counts):
        values = valid_values.get(device_id, [])
        total = context_counts[device_id]
        summary["devices"][device_id] = {
            "context_count": total,
            "valid_rssi_count": len(values),
            "valid_fraction": round(len(values) / total, 4) if total else 0.0,
            "median_rssi_dbm": statistics.median(values) if values else None,
            "standard_deviation_db": (
                round(statistics.stdev(values), 3) if len(values) > 1 else None
            ),
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
