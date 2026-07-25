"""Replay the firmware's RSSI direction detector against recorded CSV data."""

import csv
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class TrendEvent:
    time_s: float
    state: str
    delta_db: float


class TrendDetector:
    def __init__(
        self,
        *,
        window_s: float = 1.5,
        min_samples_per_window: int = 2,
        direction_threshold_db: float = 3.0,
        stable_threshold_db: float = 1.0,
        confirmations: int = 3,
        hold_s: float = 2.0,
    ) -> None:
        self.window_s = window_s
        self.min_samples_per_window = min_samples_per_window
        self.direction_threshold_db = direction_threshold_db
        self.stable_threshold_db = stable_threshold_db
        self.confirmations = confirmations
        self.hold_s = hold_s
        self.samples: list[tuple[float, float]] = []
        self.state = "unavailable"
        self.candidate = "unavailable"
        self.candidate_count = 0
        self.stable_count = 0
        self.last_directional_evidence_s = 0.0

    def add(self, timestamp_s: float, filtered_rssi_dbm: float) -> TrendEvent | None:
        previous_state = self.state
        self.samples.append((timestamp_s, filtered_rssi_dbm))
        total_s = self.window_s * 2.0
        self.samples = [
            sample
            for sample in self.samples
            if timestamp_s - sample[0] <= total_s
        ]
        recent = [
            value
            for sample_time, value in self.samples
            if timestamp_s - sample_time <= self.window_s
        ]
        previous = [
            value
            for sample_time, value in self.samples
            if self.window_s < timestamp_s - sample_time <= total_s
        ]
        if (
            len(recent) < self.min_samples_per_window
            or len(previous) < self.min_samples_per_window
        ):
            self.state = "unavailable"
            self.candidate = "unavailable"
            self.candidate_count = 0
            self.stable_count = 0
            return None

        delta_db = statistics.mean(recent) - statistics.mean(previous)
        candidate = "unavailable"
        if delta_db >= self.direction_threshold_db:
            candidate = "approaching"
        elif delta_db <= -self.direction_threshold_db:
            candidate = "receding"

        if candidate != "unavailable":
            self.stable_count = 0
            self.last_directional_evidence_s = timestamp_s
            if self.candidate == candidate:
                self.candidate_count = min(
                    self.confirmations, self.candidate_count + 1
                )
            else:
                self.candidate = candidate
                self.candidate_count = 1
            if self.candidate_count >= self.confirmations:
                self.state = candidate
        elif abs(delta_db) <= self.stable_threshold_db:
            self.candidate = "unavailable"
            self.candidate_count = 0
            self.stable_count = min(self.confirmations, self.stable_count + 1)
            if self.stable_count >= self.confirmations:
                self.state = "stable"
        else:
            self.candidate = "unavailable"
            self.candidate_count = 0
            self.stable_count = 0
            if self.state == "unavailable" or (
                self.state in ("approaching", "receding")
                and timestamp_s - self.last_directional_evidence_s > self.hold_s
            ):
                self.state = "stable"

        if self.state != previous_state:
            return TrendEvent(timestamp_s, self.state, delta_db)
        return None


def _timestamp_seconds(row: dict[str, str]) -> float:
    timestamp = (
        row.get("received_at_ns", "")
        or row.get("received_at", "")
        or row.get("context_received_at", "")
    )
    if timestamp.isdigit():
        return int(timestamp) / 1_000_000_000.0
    return datetime.fromisoformat(timestamp).timestamp()


def load_filtered_context_series(
    path: Path, device_id: str
) -> list[tuple[float, float]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    contexts: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows):
        if (
            row.get("device_id") != device_id
            or not row.get("ranging_rssi_filtered_dbm", "").strip()
        ):
            continue
        contexts[row.get("context_packet_seq") or str(index)] = row
    return sorted(
        (
            _timestamp_seconds(row),
            float(row["ranging_rssi_filtered_dbm"]),
        )
        for row in contexts.values()
    )


def replay_events(
    series: list[tuple[float, float]], *, discard_initial_s: float = 0.0
) -> list[TrendEvent]:
    if not series:
        return []
    started_at = series[0][0]
    detector = TrendDetector()
    events = []
    for timestamp_s, rssi_dbm in series:
        if timestamp_s - started_at < discard_initial_s:
            continue
        event = detector.add(timestamp_s, rssi_dbm)
        if event is not None:
            events.append(
                TrendEvent(event.time_s - started_at, event.state, event.delta_db)
            )
    return events
