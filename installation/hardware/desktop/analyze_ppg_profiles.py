"""Evaluate the six-profile MAX30102 wrist optical sweep from a wide CSV."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable, Sequence


PROFILE_FLAG_SHIFT = 5
PROFILE_FLAG_MASK = 0x07
EVALUATION_WARMUP_MS = 10_000
REFERENCE_MAX_AGE_MS = 2_000
RAW_MINIMUM = 1_000
RAW_MAXIMUM = 240_000
ADC_SATURATION = 262_143


@dataclass(frozen=True)
class OpticalProfile:
    profile_id: int
    adc_range: int
    led_amplitude: int


@dataclass(frozen=True)
class ProfileEvaluation:
    profile_id: int
    adc_range: int
    led_amplitude: int
    raw_minimum: int | None
    raw_maximum: int | None
    saturation_samples: int
    valid_results: int
    evaluated_updates: int
    availability: float
    compared_results: int
    within_five_results: int
    within_five_rate: float
    maximum_error_bpm: float | None
    passed: bool


PROFILES = (
    OpticalProfile(0, 16384, 0x7F),
    OpticalProfile(1, 8192, 0x40),
    OpticalProfile(2, 8192, 0x60),
    OpticalProfile(3, 8192, 0x7F),
    OpticalProfile(4, 16384, 0xA0),
    OpticalProfile(5, 16384, 0xC0),
)


def _integer(row: dict[str, str], field: str) -> int | None:
    value = row.get(field, "")
    return int(value) if value not in (None, "") else None


def _float(row: dict[str, str], field: str) -> float | None:
    value = row.get(field, "")
    return float(value) if value not in (None, "") else None


def _profile_at(
    timeline: Sequence[tuple[int, int]], timestamp_ms: int
) -> tuple[int, int] | None:
    preceding = [entry for entry in timeline if entry[0] <= timestamp_ms]
    if not preceding:
        return None
    start_timestamp, profile_id = preceding[-1]
    return profile_id, start_timestamp


def choose_profile(
    evaluations: Iterable[ProfileEvaluation],
) -> ProfileEvaluation | None:
    passing = [evaluation for evaluation in evaluations if evaluation.passed]
    if not passing:
        return None
    best_availability = max(evaluation.availability for evaluation in passing)
    near_best = [
        evaluation
        for evaluation in passing
        if evaluation.availability >= best_availability - 0.05
    ]
    return min(
        near_best,
        key=lambda evaluation: (
            evaluation.led_amplitude,
            -evaluation.availability,
            evaluation.adc_range,
        ),
    )


def evaluate_csv(path: Path) -> tuple[list[ProfileEvaluation], ProfileEvaluation | None]:
    with path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))

    timeline: list[tuple[int, int]] = []
    for row in rows:
        if row.get("device_id") != "person_40" or row.get("context_present") != "1":
            continue
        timestamp = _integer(row, "context_first_sample_timestamp_ms")
        flags = _integer(row, "context_flags")
        if timestamp is None or flags is None:
            continue
        profile_id = (flags >> PROFILE_FLAG_SHIFT) & PROFILE_FLAG_MASK
        if not timeline or timeline[-1][1] != profile_id:
            timeline.append((timestamp, profile_id))

    finger_results: list[tuple[int, float]] = []
    for row in rows:
        if (
            row.get("device_id") == "person_01"
            and row.get("heart_rate_updated") == "1"
        ):
            timestamp = _integer(row, "heart_rate_window_end_timestamp_ms")
            bpm = _float(row, "bpm")
            if timestamp is not None and bpm is not None:
                finger_results.append((timestamp, bpm))

    raw_by_profile: dict[int, list[int]] = {profile.profile_id: [] for profile in PROFILES}
    updates_by_profile: dict[int, list[tuple[int, float | None]]] = {
        profile.profile_id: [] for profile in PROFILES
    }
    for row in rows:
        if row.get("device_id") != "person_40":
            continue
        if row.get("ppg_present") == "1":
            timestamp = _integer(row, "ppg_first_sample_timestamp_ms")
            sample_count = _integer(row, "ppg_sample_count") or 0
            assignment = _profile_at(timeline, timestamp) if timestamp is not None else None
            if assignment is not None:
                profile_id, profile_start = assignment
                if timestamp - profile_start >= EVALUATION_WARMUP_MS:
                    for index in range(sample_count):
                        red = _integer(row, f"ppg_red_{index}")
                        infrared = _integer(row, f"ppg_ir_{index}")
                        if red is not None:
                            raw_by_profile.setdefault(profile_id, []).append(red)
                        if infrared is not None:
                            raw_by_profile.setdefault(profile_id, []).append(infrared)
        if row.get("heart_rate_updated") == "1":
            timestamp = _integer(row, "heart_rate_window_end_timestamp_ms")
            assignment = _profile_at(timeline, timestamp) if timestamp is not None else None
            if assignment is not None and timestamp is not None:
                profile_id, profile_start = assignment
                if timestamp - profile_start >= EVALUATION_WARMUP_MS:
                    updates_by_profile.setdefault(profile_id, []).append(
                        (timestamp, _float(row, "bpm"))
                    )

    evaluations: list[ProfileEvaluation] = []
    for profile in PROFILES:
        raw_values = raw_by_profile[profile.profile_id]
        updates = updates_by_profile[profile.profile_id]
        valid = [(timestamp, bpm) for timestamp, bpm in updates if bpm is not None]
        errors: list[float] = []
        for timestamp, bpm in valid:
            if not finger_results:
                continue
            reference_timestamp, reference_bpm = min(
                finger_results, key=lambda item: abs(item[0] - timestamp)
            )
            if abs(reference_timestamp - timestamp) <= REFERENCE_MAX_AGE_MS:
                errors.append(abs(float(bpm) - reference_bpm))

        within_five = sum(error <= 5.0 for error in errors)
        within_five_rate = within_five / len(errors) if errors else 0.0
        maximum_error = max(errors) if errors else None
        saturation_samples = sum(
            value <= 0 or value >= ADC_SATURATION for value in raw_values
        )
        raw_minimum = min(raw_values) if raw_values else None
        raw_maximum = max(raw_values) if raw_values else None
        availability = len(valid) / len(updates) if updates else 0.0
        passed = (
            raw_minimum is not None
            and raw_minimum >= RAW_MINIMUM
            and raw_maximum is not None
            and raw_maximum <= RAW_MAXIMUM
            and saturation_samples == 0
            and len(valid) >= 15
            and len(errors) == len(valid)
            and within_five_rate >= 0.90
            and maximum_error is not None
            and maximum_error <= 10.0
        )
        evaluations.append(
            ProfileEvaluation(
                profile_id=profile.profile_id,
                adc_range=profile.adc_range,
                led_amplitude=profile.led_amplitude,
                raw_minimum=raw_minimum,
                raw_maximum=raw_maximum,
                saturation_samples=saturation_samples,
                valid_results=len(valid),
                evaluated_updates=len(updates),
                availability=availability,
                compared_results=len(errors),
                within_five_results=within_five,
                within_five_rate=within_five_rate,
                maximum_error_bpm=maximum_error,
                passed=passed,
            )
        )
    return evaluations, choose_profile(evaluations)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a person_40 MAX30102 optical profile sweep"
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    evaluations, selected = evaluate_csv(args.csv_path)
    report = {
        "profiles": [asdict(evaluation) for evaluation in evaluations],
        "selected_profile": asdict(selected) if selected is not None else None,
        "fallback_profile": 0 if selected is None else None,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    for evaluation in evaluations:
        maximum_error = (
            f"{evaluation.maximum_error_bpm:.2f}"
            if evaluation.maximum_error_bpm is not None
            else "--"
        )
        print(
            f"profile {evaluation.profile_id}: ADC {evaluation.adc_range}, "
            f"LED 0x{evaluation.led_amplitude:02X}, "
            f"valid {evaluation.valid_results}/{evaluation.evaluated_updates}, "
            f"within5 {evaluation.within_five_rate:.1%}, "
            f"max_error {maximum_error}, passed {evaluation.passed}"
        )
    if selected is None:
        print("No profile passed; keep baseline profile 0 (ADC 16384 / LED 0x7F).")
    else:
        print(f"Selected profile {selected.profile_id}.")


if __name__ == "__main__":
    main()
