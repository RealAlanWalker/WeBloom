"""Append a deterministic emotional-flower signal stream to CSV forever."""

from __future__ import annotations

import argparse
import csv
import math
import os
import time
from datetime import datetime
from pathlib import Path


FIELDS = (
    "row_id",
    "timestamp",
    "elapsed_s",
    "phase",
    "demo_phase",
    "state",
    "growth",
    "bloom",
    "glow",
    "motion",
    "sway_x",
    "sway_y",
    "twist",
    "mist",
    "proximity",
    "connection",
    "quality",
    "sparkle",
    "pulse",
    "presence",
    "pair_ready",
    "result_ready",
    "hue",
    "brightness",
    "warmth",
    "detail",
    "branching",
)


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge1 <= edge0:
        return float(value >= edge1)
    x = clamp((value - edge0) / (edge1 - edge0))
    return x * x * (3.0 - 2.0 * x)


def gaussian(value: float, center: float, width: float) -> float:
    return math.exp(-((value - center) / width) ** 2)


def phase_name(elapsed: float) -> str:
    if elapsed < 4.0:
        return "germination"
    if elapsed < 11.0:
        return "branching"
    if elapsed < 21.0:
        return "blooming"
    return "full_bloom"


def signal_row(row_id: int, elapsed: float) -> dict[str, object]:
    growth = 0.06 + 0.94 * smoothstep(0.0, 12.0, elapsed)
    bloom = 0.015 + 0.985 * smoothstep(4.0, 21.0, elapsed)
    connection = smoothstep(2.0, 16.0, elapsed)
    proximity = 0.30 + 0.70 * smoothstep(0.5, 7.5, elapsed)
    glow = 0.20 + 0.68 * bloom + 0.05 * math.sin(elapsed * 0.72)
    motion = 0.12 + 0.13 * connection + 0.045 * math.sin(elapsed * 0.91)
    mist = 0.035 + 0.29 * bloom
    sparkle = clamp(
        gaussian(elapsed, 15.0, 0.52)
        + 0.85 * gaussian(elapsed, 19.2, 0.65)
        + 0.55 * gaussian(elapsed, 24.0, 0.78)
    )
    pulse = 0.58 + 0.20 * math.sin(elapsed * (2.8 + 1.4 * connection))
    demo_phase = min(6, int(elapsed // 4.0))
    state = min(4, int(elapsed // 5.0))
    pair_ready = float(elapsed >= 18.0)
    result_ready = float(elapsed >= 22.0)

    numeric = {
        "demo_phase": float(demo_phase),
        "state": float(state),
        "growth": growth,
        "bloom": bloom,
        "glow": clamp(glow, 0.0, 1.1),
        "motion": clamp(motion, 0.05, 0.40),
        "sway_x": 0.21 * math.sin(elapsed * 0.53) * (0.35 + 0.65 * connection),
        "sway_y": 0.085 * math.cos(elapsed * 0.39),
        "twist": 0.17 * math.sin(elapsed * 0.31) * connection,
        "mist": mist,
        "proximity": proximity,
        "connection": connection,
        "quality": 0.97,
        "sparkle": sparkle,
        "pulse": pulse,
        "presence": 1.0,
        "pair_ready": pair_ready,
        "result_ready": result_ready,
        "hue": 14.0 + 8.0 * math.sin(elapsed * 0.13),
        "brightness": 0.98 + 0.26 * bloom + 0.04 * math.sin(elapsed * 0.47),
        "warmth": 0.78 + 0.16 * bloom,
        "detail": 0.98 + 0.18 * connection,
        "branching": 0.68 + 0.34 * growth,
    }
    return {
        "row_id": row_id,
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "elapsed_s": round(elapsed, 3),
        "phase": phase_name(elapsed),
        **{name: round(value, 6) for name, value in numeric.items()},
    }


def existing_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", newline="") as stream:
        return max(0, sum(1 for _ in stream) - 1)


def run(output: Path, interval: float, reset: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if reset or not output.exists() else "a"
    row_id = 0 if mode == "w" else existing_row_count(output)
    pid_path = output.with_suffix(output.suffix + ".pid")
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    started = time.perf_counter()

    with output.open(mode, encoding="utf-8", newline="", buffering=1) as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        if mode == "w" or output.stat().st_size == 0:
            writer.writeheader()
            stream.flush()

        while True:
            cycle_started = time.perf_counter()
            elapsed = cycle_started - started
            writer.writerow(signal_row(row_id, elapsed))
            stream.flush()
            row_id += 1
            remaining = interval - (time.perf_counter() - cycle_started)
            if remaining > 0:
                time.sleep(remaining)


def write_snapshot(output: Path, elapsed: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow(signal_row(0, elapsed))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--snapshot-elapsed", type=float)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.snapshot_elapsed is not None:
        write_snapshot(output, max(0.0, args.snapshot_elapsed))
    else:
        run(output, max(0.05, args.interval), args.reset)


if __name__ == "__main__":
    main()
