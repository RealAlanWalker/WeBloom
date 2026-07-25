import csv
from datetime import datetime
import math
from pathlib import Path
import random
import statistics
import unittest

from heart_rate import HeartRateAnalyzer, _BeatEstimate


SAMPLE_RATE_HZ = 100
SAMPLE_COUNT = 800
TARGET_BPM = 90.0


def sine_ir(sequence: int, bpm: float = TARGET_BPM, amplitude: float = 500.0) -> float:
    seconds = sequence / SAMPLE_RATE_HZ
    return 22000.0 + amplitude * math.sin(2.0 * math.pi * bpm * seconds / 60.0)


def analyzer_with_skipped_blocks(skip_blocks: set[int]) -> HeartRateAnalyzer:
    analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
    for first_sequence in range(0, SAMPLE_COUNT, 10):
        block = first_sequence // 10
        if block in skip_blocks:
            continue
        analyzer.add_samples(
            sample_rate_hz=SAMPLE_RATE_HZ,
            packet_sequence=block,
            first_sample_sequence=first_sequence,
            first_sample_timestamp_ms=first_sequence * 10,
            ir_values=[sine_ir(sequence) for sequence in range(first_sequence, first_sequence + 10)],
        )
    return analyzer


def evenly_spaced_blocks(count: int) -> set[int]:
    # Keep the first and last blocks so the intended missing percentage is exact.
    candidates = list(range(2, 78))
    return {candidates[index * len(candidates) // count] for index in range(count)}


def physiological_ir(
    sequence: int,
    bpm: float,
    *,
    width: float = 0.055,
    dicrotic_ratio: float = 0.25,
    alternating_ratio: float = 1.0,
    noise: float = 5.0,
    baseline_drift: float = 50.0,
    phase: float = 0.13,
    dc: float = 22000.0,
    amplitude: float = 500.0,
) -> float:
    """Generate an asymmetric pulse train with known beat timing."""

    seconds = sequence / SAMPLE_RATE_HZ
    cycles = seconds * bpm / 60.0 + phase
    beat_index = math.floor(cycles)
    cycle_phase = cycles - beat_index
    beat_scale = 1.0 if beat_index % 2 == 0 else alternating_ratio
    primary = math.exp(-0.5 * ((cycle_phase - 0.16) / width) ** 2)
    dicrotic = math.exp(
        -0.5 * ((cycle_phase - 0.48) / (width * 1.35)) ** 2
    )
    deterministic_noise = noise * (
        0.55 * math.sin(sequence * 1.731)
        + 0.45 * math.sin(sequence * 2.417)
    )
    return (
        dc
        + amplitude * beat_scale * (primary + dicrotic_ratio * dicrotic)
        + baseline_drift * math.sin(2.0 * math.pi * 0.09 * seconds)
        + deterministic_noise
    )


def analyzer_for_physiological_signal(
    bpm: float,
    *,
    seconds: int = 16,
    skipped_blocks: set[int] | None = None,
    **waveform: float,
) -> HeartRateAnalyzer:
    # Contact behavior has its own tests.  Disabling it here isolates whether
    # the beat estimator works uniformly over morphology and heart-rate range.
    analyzer = HeartRateAnalyzer(
        SAMPLE_RATE_HZ,
        min_bpm=40.0,
        contact_ir_threshold=0.0,
        contact_step_ratio=0.99,
        contact_drift_ratio=0.98,
    )
    skipped_blocks = skipped_blocks or set()
    for first_sequence in range(0, seconds * SAMPLE_RATE_HZ, 10):
        block = first_sequence // 10
        if block in skipped_blocks:
            continue
        analyzer.add_samples(
            sample_rate_hz=SAMPLE_RATE_HZ,
            packet_sequence=block,
            first_sample_sequence=first_sequence,
            first_sample_timestamp_ms=first_sequence * 10,
            ir_values=[
                physiological_ir(sequence, bpm, **waveform)
                for sequence in range(first_sequence, first_sequence + 10)
            ],
        )
    return analyzer


def replay_wide_capture(path: Path, devices: set[str]) -> dict[str, list]:
    analyzers: dict[str, HeartRateAnalyzer] = {}
    replay: dict[str, list] = {device: [] for device in devices}
    with path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            device = row["device_id"]
            if device not in replay or row["ppg_present"] != "1":
                continue
            sample_rate = int(row["ppg_sample_rate_hz"])
            sample_count = int(row["ppg_sample_count"])
            analyzer = analyzers.setdefault(device, HeartRateAnalyzer(sample_rate))
            analyzer.add_samples(
                sample_rate_hz=sample_rate,
                packet_sequence=int(row["ppg_packet_seq"]),
                first_sample_sequence=int(row["ppg_first_sample_seq"]),
                first_sample_timestamp_ms=int(row["ppg_first_sample_timestamp_ms"]),
                ir_values=[
                    float(row[f"ppg_ir_{index}"])
                    for index in range(sample_count)
                ],
            )
            if row["heart_rate_updated"] == "1":
                replay[device].append(
                    (datetime.fromisoformat(row["row_received_at"]), analyzer.analyze())
                )
    return replay


class HeartRateAnalyzerTests(unittest.TestCase):
    def assert_bpm_close(self, actual: float | None, tolerance: float = 3.0) -> None:
        self.assertIsNotNone(actual)
        self.assertLessEqual(abs(float(actual) - TARGET_BPM), tolerance)

    def test_90_bpm_without_gaps_is_good(self) -> None:
        result = analyzer_with_skipped_blocks(set()).analyze()
        self.assertEqual(result.state, "good")
        self.assert_bpm_close(result.bpm, tolerance=2.0)
        self.assertEqual(result.sample_missing_rate, 0.0)
        self.assertGreater(result.quality, 0.9)

    def test_physiological_waveforms_cover_full_configured_range(self) -> None:
        profiles = (
            {
                "width": 0.050,
                "dicrotic_ratio": 0.20,
                "alternating_ratio": 1.0,
                "noise": 5.0,
                "baseline_drift": 50.0,
            },
            {
                "width": 0.070,
                "dicrotic_ratio": 0.75,
                "alternating_ratio": 1.0,
                "noise": 8.0,
                "baseline_drift": 80.0,
            },
            {
                "width": 0.055,
                "dicrotic_ratio": 0.25,
                "alternating_ratio": 0.35,
                "noise": 8.0,
                "baseline_drift": 100.0,
            },
        )
        for profile in profiles:
            for bpm in range(40, 201, 10):
                with self.subTest(profile=profile, bpm=bpm):
                    result = analyzer_for_physiological_signal(
                        float(bpm), **profile
                    ).analyze()
                    self.assertIn(result.state, ("good", "degraded"))
                    self.assertIsNotNone(result.bpm)
                    self.assertLessEqual(abs(float(result.bpm) - bpm) / bpm, 0.05)

    def test_true_low_rate_and_alternating_high_rate_are_not_harmonic_aliases(self) -> None:
        waveform = {
            "alternating_ratio": 0.35,
            "dicrotic_ratio": 0.25,
            "noise": 8.0,
            "baseline_drift": 100.0,
        }
        low = analyzer_for_physiological_signal(42.5, **waveform).analyze()
        high = analyzer_for_physiological_signal(85.0, **waveform).analyze()
        self.assertAlmostEqual(float(low.bpm), 42.5, delta=42.5 * 0.05)
        self.assertAlmostEqual(float(high.bpm), 85.0, delta=85.0 * 0.05)

    def test_established_beat_train_survives_slow_optical_waves(self) -> None:
        """A later slow pressure wave must not replace individual beats."""

        for bpm in (70.0, 85.0, 110.0, 150.0):
            with self.subTest(bpm=bpm):
                analyzer = HeartRateAnalyzer(
                    SAMPLE_RATE_HZ,
                    contact_ir_threshold=0.0,
                    contact_step_ratio=0.99,
                    contact_drift_ratio=0.98,
                )
                artifact_results = []
                for second in range(24):
                    for first_sequence in range(
                        second * SAMPLE_RATE_HZ,
                        (second + 1) * SAMPLE_RATE_HZ,
                        10,
                    ):
                        values = []
                        for sequence in range(first_sequence, first_sequence + 10):
                            seconds = sequence / SAMPLE_RATE_HZ
                            cycles = seconds * bpm / 60.0 + 0.13
                            cycle_phase = cycles - math.floor(cycles)
                            pulse = math.exp(
                                -0.5 * ((cycle_phase - 0.16) / 0.055) ** 2
                            ) + 0.25 * math.exp(
                                -0.5 * ((cycle_phase - 0.48) / 0.075) ** 2
                            )
                            artifact_amplitude = 0.0 if second < 10 else 45.0
                            slow_artifact = artifact_amplitude * (
                                0.58
                                * math.sin(
                                    2.0 * math.pi * 42.0 * seconds / 60.0 + 0.4
                                )
                                + 0.42
                                * math.sin(
                                    2.0 * math.pi * 58.0 * seconds / 60.0 + 1.2
                                )
                            )
                            noise = 3.0 * (
                                0.55 * math.sin(sequence * 1.731)
                                + 0.45 * math.sin(sequence * 2.417)
                            )
                            values.append(
                                22000.0 + 60.0 * pulse + slow_artifact + noise
                            )
                        analyzer.add_samples(
                            sample_rate_hz=SAMPLE_RATE_HZ,
                            packet_sequence=first_sequence // 10,
                            first_sample_sequence=first_sequence,
                            first_sample_timestamp_ms=first_sequence * 10,
                            ir_values=values,
                        )
                    result = analyzer.analyze()
                    if second >= 10 and result.bpm is not None:
                        artifact_results.append(float(result.bpm))

                self.assertGreaterEqual(len(artifact_results), 10)
                self.assertTrue(
                    all(abs(result - bpm) / bpm <= 0.05 for result in artifact_results)
                )

    def test_confirmed_tracker_allows_a_real_rate_change(self) -> None:
        analyzer = HeartRateAnalyzer(
            SAMPLE_RATE_HZ,
            contact_ir_threshold=0.0,
            contact_step_ratio=0.99,
            contact_drift_ratio=0.98,
        )
        results = []
        for second in range(28):
            bpm = 80.0 if second < 12 else 120.0
            for first_sequence in range(
                second * SAMPLE_RATE_HZ,
                (second + 1) * SAMPLE_RATE_HZ,
                10,
            ):
                analyzer.add_samples(
                    sample_rate_hz=SAMPLE_RATE_HZ,
                    packet_sequence=first_sequence // 10,
                    first_sample_sequence=first_sequence,
                    first_sample_timestamp_ms=first_sequence * 10,
                    ir_values=[
                        physiological_ir(sequence, bpm)
                        for sequence in range(first_sequence, first_sequence + 10)
                    ],
                )
            results.append(analyzer.analyze())

        final_bpms = [result.bpm for result in results[-5:]]
        self.assertTrue(all(bpm is not None for bpm in final_bpms))
        self.assertTrue(
            all(abs(float(bpm) - 120.0) / 120.0 <= 0.05 for bpm in final_bpms)
        )

    def test_bpm_is_invariant_to_dc_gain_and_window_phase(self) -> None:
        for dc in (12000.0, 22000.0, 80000.0):
            for amplitude in (200.0, 800.0):
                for phase in (0.0, 0.37, 0.72):
                    with self.subTest(dc=dc, amplitude=amplitude, phase=phase):
                        result = analyzer_for_physiological_signal(
                            85.0,
                            alternating_ratio=0.35,
                            dicrotic_ratio=0.25,
                            noise=8.0,
                            baseline_drift=100.0,
                            dc=dc,
                            amplitude=amplitude,
                            phase=phase,
                        ).analyze()
                        self.assertIsNotNone(result.bpm)
                        self.assertLessEqual(abs(float(result.bpm) - 85.0) / 85.0, 0.05)

    def test_physiological_signal_tolerates_distributed_packet_gaps(self) -> None:
        skipped = {block for block in range(20, 160) if block % 13 == 0}
        result = analyzer_for_physiological_signal(
            130.0,
            skipped_blocks=skipped,
            alternating_ratio=0.35,
            dicrotic_ratio=0.25,
            noise=15.0,
            baseline_drift=100.0,
        ).analyze()
        self.assertIn(result.state, ("good", "degraded"))
        self.assertIsNotNone(result.bpm)
        self.assertLessEqual(abs(float(result.bpm) - 130.0) / 130.0, 0.05)
        self.assertGreater(result.sample_missing_rate, 0.0)

    def test_contact_states_suppress_uncovered_and_recently_moved_sensor(self) -> None:
        uncovered = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        for first_sequence in range(0, 800, 10):
            uncovered.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=first_sequence // 10,
                first_sample_sequence=first_sequence,
                first_sample_timestamp_ms=first_sequence * 10,
                ir_values=[600.0] * 10,
            )
        self.assertEqual(uncovered.analyze().state, "no_contact")

        moved = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        values = [22000.0] * 400 + [100000.0] * 100
        for first_sequence in range(0, len(values), 10):
            moved.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=first_sequence // 10,
                first_sample_sequence=first_sequence,
                first_sample_timestamp_ms=first_sequence * 10,
                ir_values=values[first_sequence:first_sequence + 10],
            )
        result = moved.analyze()
        self.assertEqual(result.state, "contact_unstable")
        self.assertIsNone(result.bpm)

    def test_short_gap_quality_boundaries(self) -> None:
        for missing_blocks, expected_state in (
            (4, "good"),   # 5%
            (8, "good"),   # 10%
            (14, "degraded"),  # 17.5%
            (16, "degraded"),  # 20%
        ):
            with self.subTest(missing_blocks=missing_blocks):
                result = analyzer_with_skipped_blocks(
                    evenly_spaced_blocks(missing_blocks)
                ).analyze()
                self.assertEqual(result.state, expected_state)
                self.assert_bpm_close(result.bpm)
                self.assertAlmostEqual(
                    result.sample_missing_rate,
                    missing_blocks * 10 / SAMPLE_COUNT * 100.0,
                )
                self.assertLessEqual(result.max_gap_ms, 500)

    def test_more_than_20_percent_missing_suppresses_bpm(self) -> None:
        result = analyzer_with_skipped_blocks(evenly_spaced_blocks(17)).analyze()
        self.assertEqual(result.state, "too_many_gaps")
        self.assertIsNone(result.bpm)
        self.assertGreater(result.sample_missing_rate, 20.0)

    def test_gap_over_500_ms_discards_earlier_window(self) -> None:
        analyzer = analyzer_with_skipped_blocks({40, 41, 42, 43, 44, 45})
        result = analyzer.analyze()
        self.assertEqual(result.state, "long_gap")
        self.assertIsNone(result.bpm)
        self.assertLess(result.observed_samples, 400)

    def test_exactly_500_ms_gap_is_interpolated(self) -> None:
        result = analyzer_with_skipped_blocks({40, 41, 42, 43, 44}).analyze()
        self.assertEqual(result.state, "good")
        self.assert_bpm_close(result.bpm)
        self.assertEqual(result.max_gap_ms, 500)

    def test_300_ms_gap_is_interpolated(self) -> None:
        result = analyzer_with_skipped_blocks({40, 41, 42}).analyze()
        self.assertEqual(result.state, "good")
        self.assert_bpm_close(result.bpm)
        self.assertEqual(result.max_gap_ms, 300)

    def test_packet_gap_does_not_break_contiguous_samples(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        for block in range(80):
            analyzer.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=block + (5 if block >= 20 else 0),
                first_sample_sequence=block * 10,
                first_sample_timestamp_ms=block * 100,
                ir_values=[sine_ir(sequence) for sequence in range(block * 10, block * 10 + 10)],
            )
        result = analyzer.analyze()
        self.assertEqual(result.state, "good")
        self.assertEqual(result.sample_missing_rate, 0.0)
        self.assert_bpm_close(result.bpm)

    def test_low_signal_has_explicit_state(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        for first_sequence in range(0, 500, 10):
            analyzer.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=first_sequence // 10,
                first_sample_sequence=first_sequence,
                first_sample_timestamp_ms=first_sequence * 10,
                ir_values=[22000.0] * 10,
            )
        result = analyzer.analyze()
        self.assertEqual(result.state, "low_signal")
        self.assertIsNone(result.bpm)

    def test_low_periodicity_has_explicit_state(self) -> None:
        randomizer = random.Random(12345)
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        for first_sequence in range(0, SAMPLE_COUNT, 10):
            analyzer.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=first_sequence // 10,
                first_sample_sequence=first_sequence,
                first_sample_timestamp_ms=first_sequence * 10,
                ir_values=[22000.0 + randomizer.uniform(-500, 500) for _ in range(10)],
            )
        result = analyzer.analyze()
        self.assertEqual(result.state, "low_periodicity")
        self.assertIsNone(result.bpm)
        self.assertLess(result.periodicity, 0.35)

    def test_duplicate_and_out_of_order_packets_are_ignored(self) -> None:
        analyzer = analyzer_with_skipped_blocks(set(range(50, 80)))
        before = analyzer.analyze()
        original_count = analyzer.sample_count
        for packet_sequence in (49, 48):
            changed = analyzer.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=packet_sequence,
                first_sample_sequence=packet_sequence * 10,
                first_sample_timestamp_ms=packet_sequence * 100,
                ir_values=[0.0] * 10,
            )
            self.assertFalse(changed)
        after = analyzer.analyze()
        self.assertEqual(analyzer.sample_count, original_count)
        self.assertEqual(after.window_end_sample_seq, before.window_end_sample_seq)
        self.assertEqual(after.bpm, before.bpm)

    def test_restart_and_sample_rate_change_are_reported_while_rewarming(self) -> None:
        analyzer = analyzer_with_skipped_blocks(set(range(31, 80)))
        restarted = analyzer.add_samples(
            sample_rate_hz=100,
            packet_sequence=0,
            first_sample_sequence=0,
            first_sample_timestamp_ms=0,
            ir_values=[sine_ir(sequence) for sequence in range(10)],
        )
        self.assertTrue(restarted)
        self.assertEqual(analyzer.analyze().state, "restarted")

        changed = analyzer.add_samples(
            sample_rate_hz=50,
            packet_sequence=1,
            first_sample_sequence=10,
            first_sample_timestamp_ms=200,
            ir_values=[sine_ir(sequence) for sequence in range(10, 20)],
        )
        self.assertTrue(changed)
        self.assertEqual(analyzer.analyze().state, "sample_rate_changed")

    def test_devices_are_isolated_and_context_is_ignored(self) -> None:
        device_a = analyzer_with_skipped_blocks(set())
        device_b = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        for first_sequence in range(0, SAMPLE_COUNT, 10):
            device_b.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=first_sequence // 10,
                first_sample_sequence=first_sequence,
                first_sample_timestamp_ms=first_sequence * 10,
                ir_values=[sine_ir(sequence, bpm=120.0) for sequence in range(first_sequence, first_sequence + 10)],
            )
        before = device_a.sample_count
        self.assertFalse(device_a.add_packet({"type": "sensor_context_packet"}))
        self.assertEqual(device_a.sample_count, before)
        self.assert_bpm_close(device_a.analyze().bpm, tolerance=2.0)
        self.assertAlmostEqual(float(device_b.analyze().bpm), 120.0, delta=3.0)

    def test_recovers_after_sustained_packet_loss(self) -> None:
        """After 15% loss for 30s, good data should yield BPM within 5s."""
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        randomizer = random.Random(42)
        seq = 0
        packet_seq = 0
        # Phase 1: 30 seconds with 15% random packet loss
        for _ in range(300):  # 300 packets = 30s at 10 packets/s
            if randomizer.random() < 0.15:
                seq += 10
                packet_seq += 1
                continue
            analyzer.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=packet_seq,
                first_sample_sequence=seq,
                first_sample_timestamp_ms=seq * 10,
                ir_values=[sine_ir(s) for s in range(seq, seq + 10)],
            )
            seq += 10
            packet_seq += 1
        # Phase 2: 5 seconds of perfect data
        for _ in range(50):
            analyzer.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=packet_seq,
                first_sample_sequence=seq,
                first_sample_timestamp_ms=seq * 10,
                ir_values=[sine_ir(s) for s in range(seq, seq + 10)],
            )
            seq += 10
            packet_seq += 1
        result = analyzer.analyze()
        self.assertIn(result.state, ("good", "degraded"))
        self.assert_bpm_close(result.bpm)

    def test_motion_artifact_does_not_produce_good_bpm(self) -> None:
        """Large non-periodic swings should not yield a good BPM."""
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        randomizer = random.Random(99)
        for first_sequence in range(0, SAMPLE_COUNT, 10):
            # Simulate motion: large amplitude random walk, no cardiac rhythm
            base = 22000.0 + randomizer.uniform(-3000, 3000)
            analyzer.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=first_sequence // 10,
                first_sample_sequence=first_sequence,
                first_sample_timestamp_ms=first_sequence * 10,
                ir_values=[base + randomizer.uniform(-2000, 2000) for _ in range(10)],
            )
        result = analyzer.analyze()
        self.assertNotEqual(result.state, "good")
        self.assertNotEqual(result.state, "degraded")

    def test_weak_local_beats_with_spectral_support_are_degraded(self) -> None:
        """Weak local beats may qualify without passing global prominence."""

        bpm = 85.0
        period = SAMPLE_RATE_HZ * 60.0 / bpm
        jitter_pattern = (0, 4, -3, 2, -4, 1)
        beat_centers: list[float] = []
        beat_index = -2
        while True:
            center = (
                (beat_index + 0.03) * period
                + 3.0 * jitter_pattern[beat_index % len(jitter_pattern)]
            )
            if center >= SAMPLE_COUNT:
                break
            if center >= 0.0:
                beat_centers.append(center)
            beat_index += 1

        values: list[float] = []
        for sequence in range(SAMPLE_COUNT):
            pulse = sum(
                math.exp(-0.5 * ((sequence - center) / 4.0) ** 2)
                + 0.2
                * math.exp(
                    -0.5 * ((sequence - (center + 0.32 * period)) / 5.4) ** 2
                )
                for center in beat_centers
            )
            seconds = sequence / SAMPLE_RATE_HZ
            values.append(
                22000.0
                + 5.0 * pulse
                + 400.0 * math.sin(2.0 * math.pi * 0.09 * seconds)
                + 10.0 * math.sin(2.0 * math.pi * 0.47 * seconds + 0.3)
                + 2.0
                * (
                    0.55 * math.sin(sequence * 1.731)
                    + 0.45 * math.sin(sequence * 2.417)
                )
            )

        analyzer = HeartRateAnalyzer(
            SAMPLE_RATE_HZ,
            contact_ir_threshold=0.0,
            contact_step_ratio=0.99,
            contact_drift_ratio=0.98,
        )
        results = []
        for repetition in range(3):
            sequence_offset = repetition * SAMPLE_COUNT
            for first_sequence in range(0, SAMPLE_COUNT, 10):
                absolute_sequence = sequence_offset + first_sequence
                analyzer.add_samples(
                    sample_rate_hz=SAMPLE_RATE_HZ,
                    packet_sequence=absolute_sequence // 10,
                    first_sample_sequence=absolute_sequence,
                    first_sample_timestamp_ms=absolute_sequence * 10,
                    ir_values=values[first_sequence:first_sequence + 10],
                )
            results.append(analyzer.analyze())

        filtered = analyzer._bandpass(values)
        minimum_lag, maximum_lag = analyzer._lag_bounds(len(filtered))
        global_peaks = analyzer._detect_peaks(
            filtered, minimum_lag, maximum_lag
        )
        interior_centers = [
            center for center in beat_centers if 10.0 < center < SAMPLE_COUNT - 10
        ]
        globally_detected_beats = sum(
            any(abs(peak.index - center) <= 6.0 for peak in global_peaks)
            for center in interior_centers
        )
        evidence_filtered = analyzer._evidence_bandpass(values)
        estimate = analyzer._estimate_beats(filtered, evidence_filtered)
        result = results[-1]

        self.assertLess(globally_detected_beats, len(interior_centers))
        self.assertIsNotNone(estimate)
        self.assertGreaterEqual(estimate.matched_peak_count, 5)
        self.assertFalse(estimate.strong_evidence)
        self.assertEqual(
            [pending.state for pending in results[:2]],
            ["low_periodicity", "low_periodicity"],
        )
        self.assertEqual(result.state, "degraded")
        self.assertAlmostEqual(float(result.bpm), bpm, delta=bpm * 0.05)

    def test_contact_locks_through_slow_drift_and_resets_on_step(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        sequence = 0

        def add_second(packet_sequence: int, dc: float) -> str:
            nonlocal sequence
            values = [
                dc
                + 25.0
                * math.sin(
                    2.0 * math.pi * 85.0 * sample / SAMPLE_RATE_HZ / 60.0
                )
                for sample in range(sequence, sequence + SAMPLE_RATE_HZ)
            ]
            analyzer.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=packet_sequence,
                first_sample_sequence=sequence,
                first_sample_timestamp_ms=sequence * 10,
                ir_values=values,
            )
            sequence += SAMPLE_RATE_HZ
            return analyzer.analyze().state

        warmup_states = [
            add_second(second, 100000.0 * (1.0 + 0.0015 * second))
            for second in range(4)
        ]
        self.assertEqual(warmup_states[:3], ["contact_unstable"] * 3)
        self.assertNotEqual(warmup_states[3], "contact_unstable")

        slow_drift_states = [
            add_second(second, 100000.0 * (1.0 + 0.0015 * second))
            for second in range(4, 8)
        ]
        self.assertNotIn("contact_unstable", slow_drift_states)

        previous_dc = 100000.0 * (1.0 + 0.0015 * 7)
        self.assertEqual(add_second(8, previous_dc * 1.04), "contact_unstable")

    def test_contact_settles_through_small_ring_baseline_changes(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        sequence = 0

        def add_second(packet_sequence: int, dc: float) -> str:
            nonlocal sequence
            analyzer.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=packet_sequence,
                first_sample_sequence=sequence,
                first_sample_timestamp_ms=sequence * 10,
                ir_values=[
                    dc
                    + 25.0
                    * math.sin(2.0 * math.pi * 85.0 * sample / 6000.0)
                    for sample in range(sequence, sequence + SAMPLE_RATE_HZ)
                ],
            )
            sequence += SAMPLE_RATE_HZ
            return analyzer.analyze().state

        states = [
            add_second(second, 100000.0 * (1.0 + 0.004 * second))
            for second in range(4)
        ]

        self.assertEqual(states[:3], ["contact_unstable"] * 3)
        self.assertNotEqual(states[3], "contact_unstable")

    def test_partial_second_removal_suppresses_bpm_immediately(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        for second in range(8):
            first_sequence = second * SAMPLE_RATE_HZ
            analyzer.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=second,
                first_sample_sequence=first_sequence,
                first_sample_timestamp_ms=first_sequence * 10,
                ir_values=[
                    sine_ir(sequence, bpm=80.0, amplitude=40.0) + 78000.0
                    for sequence in range(
                        first_sequence, first_sequence + SAMPLE_RATE_HZ
                    )
                ],
            )
        self.assertIsNotNone(analyzer.analyze().bpm)

        analyzer.add_samples(
            sample_rate_hz=SAMPLE_RATE_HZ,
            packet_sequence=8,
            first_sample_sequence=800,
            first_sample_timestamp_ms=8000,
            ir_values=[500.0] * 25,
        )
        removed = analyzer.analyze()
        self.assertEqual(removed.state, "no_contact")
        self.assertIsNone(removed.bpm)

    def test_latest_weak_wrist_replay_meets_availability_target(self) -> None:
        """Replay checks availability and harmonics, not absolute BPM truth."""

        path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "sensor_data_20260723_223952.csv"
        )
        if not path.exists():
            self.skipTest("local recorded PPG fixture is not available")
        analyzers: dict[str, HeartRateAnalyzer] = {}
        packet_counts: dict[str, int] = {}
        results: dict[str, list] = {"person_40": [], "person_01": []}
        with path.open(encoding="utf-8", newline="") as source:
            for row in csv.DictReader(source):
                device = row["device_id"]
                if device not in results or row["ppg_present"] != "1":
                    continue
                sample_rate = int(row["ppg_sample_rate_hz"])
                sample_count = int(row["ppg_sample_count"])
                analyzer = analyzers.setdefault(
                    device, HeartRateAnalyzer(sample_rate)
                )
                analyzer.add_samples(
                    sample_rate_hz=sample_rate,
                    packet_sequence=int(row["ppg_packet_seq"]),
                    first_sample_sequence=int(row["ppg_first_sample_seq"]),
                    first_sample_timestamp_ms=int(
                        row["ppg_first_sample_timestamp_ms"]
                    ),
                    ir_values=[
                        float(row[f"ppg_ir_{index}"])
                        for index in range(sample_count)
                    ],
                )
                packet_counts[device] = packet_counts.get(device, 0) + 1
                if packet_counts[device] % 10 == 0:
                    results[device].append(analyzer.analyze())

        valid_bpms = [
            result.bpm
            for result in results["person_40"]
            if result.bpm is not None
        ]
        # These endpoints were selected offline by an independent 0.6--3 Hz
        # Butterworth + peak-IBI/FFT agreement check.  Keeping only the
        # endpoints here avoids adding SciPy as a project/test dependency.
        independently_eligible_ends = {
            318456, 318556, 318656, 318756, 318856, 318956,
            319056, 319156, 319256, 319366, 319466, 319566,
            319666, 319766, 319866, 320066, 320166, 320266,
            320366, 320466, 320576, 320676, 320776, 320876,
            320976, 321076,
        }
        eligible_results = [
            result
            for result in results["person_40"]
            if result.window_end_sample_seq in independently_eligible_ends
        ]
        eligible_bpms = [
            result.bpm for result in eligible_results if result.bpm is not None
        ]
        self.assertEqual(len(eligible_results), 26)
        self.assertGreaterEqual(len(eligible_bpms), math.ceil(26 * 0.75))
        for previous, current in zip(valid_bpms, valid_bpms[1:]):
            ratio = max(previous, current) / min(previous, current)
            self.assertLess(ratio, 1.45)

        self.assertTrue(results["person_01"])
        self.assertTrue(
            all(result.state == "no_contact" for result in results["person_01"])
        )

    def test_paired_finger_and_wrist_replay_rejects_wrist_outliers(self) -> None:
        """The finger is only an offline reference; production remains isolated."""

        path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "sensor_data_20260723_231451.csv"
        )
        if not path.exists():
            self.skipTest("local recorded PPG fixture is not available")
        analyzers: dict[str, HeartRateAnalyzer] = {}
        replay: dict[str, list[tuple[float, object]]] = {
            "person_01": [],
            "person_40": [],
        }
        with path.open(encoding="utf-8", newline="") as source:
            for row in csv.DictReader(source):
                device = row["device_id"]
                if device not in replay or row["ppg_present"] != "1":
                    continue
                sample_rate = int(row["ppg_sample_rate_hz"])
                sample_count = int(row["ppg_sample_count"])
                analyzer = analyzers.setdefault(
                    device, HeartRateAnalyzer(sample_rate)
                )
                analyzer.add_samples(
                    sample_rate_hz=sample_rate,
                    packet_sequence=int(row["ppg_packet_seq"]),
                    first_sample_sequence=int(row["ppg_first_sample_seq"]),
                    first_sample_timestamp_ms=int(
                        row["ppg_first_sample_timestamp_ms"]
                    ),
                    ir_values=[
                        float(row[f"ppg_ir_{index}"])
                        for index in range(sample_count)
                    ],
                )
                if row["heart_rate_updated"] == "1":
                    replay[device].append(
                        (
                            datetime.fromisoformat(row["row_received_at"]).timestamp(),
                            analyzer.analyze(),
                        )
                    )

        finger_valid = [
            (timestamp, result.bpm)
            for timestamp, result in replay["person_01"]
            if result.bpm is not None
        ]
        wrist_valid = [
            (timestamp, result.bpm)
            for timestamp, result in replay["person_40"]
            if result.bpm is not None
        ]
        self.assertGreaterEqual(len(finger_valid), 40)
        self.assertGreaterEqual(len(wrist_valid), 25)

        errors = []
        for timestamp, wrist_bpm in wrist_valid:
            _, finger_bpm = min(
                finger_valid, key=lambda item: abs(item[0] - timestamp)
            )
            errors.append(abs(float(wrist_bpm) - float(finger_bpm)))
        self.assertGreaterEqual(
            sum(error <= 5.0 for error in errors),
            math.ceil(0.90 * len(errors)),
        )
        self.assertLessEqual(max(errors), 10.0)

        stable_start = datetime.fromisoformat(
            "2026-07-23T23:15:41+08:00"
        ).timestamp()
        stable_end = datetime.fromisoformat(
            "2026-07-23T23:16:01.999+08:00"
        ).timestamp()
        stable_valid = [
            result
            for timestamp, result in replay["person_40"]
            if stable_start <= timestamp <= stable_end and result.bpm is not None
        ]
        self.assertGreaterEqual(len(stable_valid), 19)

        final_result = replay["person_40"][-1][1]
        self.assertIsNone(final_result.bpm)
        self.assertIn(final_result.state, ("no_contact", "contact_unstable"))

    def test_latest_paired_replay_rejects_persistent_slow_artifact(self) -> None:
        """A slow optical wave must not replace the denser wrist beat train."""

        path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "sensor_data_20260724_001206.csv"
        )
        if not path.exists():
            self.skipTest("local recorded PPG fixture is not available")
        analyzers: dict[str, HeartRateAnalyzer] = {}
        replay: dict[str, list[tuple[float, object]]] = {
            "person_01": [],
            "person_40": [],
        }
        with path.open(encoding="utf-8", newline="") as source:
            for row in csv.DictReader(source):
                device = row["device_id"]
                if device not in replay or row["ppg_present"] != "1":
                    continue
                sample_rate = int(row["ppg_sample_rate_hz"])
                sample_count = int(row["ppg_sample_count"])
                analyzer = analyzers.setdefault(
                    device, HeartRateAnalyzer(sample_rate)
                )
                analyzer.add_samples(
                    sample_rate_hz=sample_rate,
                    packet_sequence=int(row["ppg_packet_seq"]),
                    first_sample_sequence=int(row["ppg_first_sample_seq"]),
                    first_sample_timestamp_ms=int(
                        row["ppg_first_sample_timestamp_ms"]
                    ),
                    ir_values=[
                        float(row[f"ppg_ir_{index}"])
                        for index in range(sample_count)
                    ],
                )
                if row["heart_rate_updated"] == "1":
                    replay[device].append(
                        (
                            datetime.fromisoformat(row["row_received_at"]).timestamp(),
                            analyzer.analyze(),
                        )
                    )

        finger_valid = [
            (timestamp, result.bpm)
            for timestamp, result in replay["person_01"]
            if result.bpm is not None
        ]
        wrist_valid = [
            (timestamp, result.bpm)
            for timestamp, result in replay["person_40"]
            if result.bpm is not None
        ]
        self.assertGreaterEqual(len(finger_valid), 90)
        self.assertGreaterEqual(len(wrist_valid), 55)

        errors = []
        for timestamp, wrist_bpm in wrist_valid:
            _, finger_bpm = min(
                finger_valid, key=lambda item: abs(item[0] - timestamp)
            )
            errors.append(abs(float(wrist_bpm) - float(finger_bpm)))
        self.assertGreaterEqual(
            sum(error <= 5.0 for error in errors),
            math.ceil(0.90 * len(errors)),
        )
        self.assertLessEqual(max(errors), 10.0)

        stable_start = datetime.fromisoformat(
            "2026-07-24T00:13:35+08:00"
        ).timestamp()
        stable_valid = [
            result
            for timestamp, result in replay["person_40"]
            if timestamp >= stable_start and result.bpm is not None
        ]
        self.assertGreaterEqual(len(stable_valid), 25)

    def test_recorded_stationary_wrist_does_not_switch_harmonics(self) -> None:
        """The capture is a stability regression, not an absolute BPM oracle."""

        path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "sensor_data_20260723_215813.csv"
        )
        if not path.exists():
            self.skipTest("local recorded PPG fixture is not available")
        analyzer: HeartRateAnalyzer | None = None
        packet_count = 0
        valid_bpms: list[float] = []
        with path.open(encoding="utf-8", newline="") as source:
            for row in csv.DictReader(source):
                if row["device_id"] != "person_40" or row["ppg_present"] != "1":
                    continue
                sample_rate = int(row["ppg_sample_rate_hz"])
                sample_count = int(row["ppg_sample_count"])
                if analyzer is None:
                    analyzer = HeartRateAnalyzer(sample_rate)
                analyzer.add_samples(
                    sample_rate_hz=sample_rate,
                    packet_sequence=int(row["ppg_packet_seq"]),
                    first_sample_sequence=int(row["ppg_first_sample_seq"]),
                    first_sample_timestamp_ms=int(row["ppg_first_sample_timestamp_ms"]),
                    ir_values=[
                        float(row[f"ppg_ir_{index}"])
                        for index in range(sample_count)
                    ],
                )
                packet_count += 1
                if packet_count % 10 == 0:
                    result = analyzer.analyze()
                    if result.bpm is not None:
                        valid_bpms.append(result.bpm)

        self.assertGreaterEqual(len(valid_bpms), 10)
        for previous, current in zip(valid_bpms, valid_bpms[1:]):
            ratio = current / previous
            self.assertGreater(ratio, 0.70)
            self.assertLess(ratio, 1.30)
        self.assertLess(max(valid_bpms) / min(valid_bpms), 1.35)

    def test_morphology_template_penalizes_alternating_peak_shapes(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        values = [0.0] * SAMPLE_COUNT
        primary_indices: list[int] = []
        dense_indices: list[int] = []
        for center in range(50, 701, 100):
            primary_indices.append(center)
            dense_indices.extend((center, center + 50))
            for index in range(center - 20, center + 21):
                values[index] += math.exp(-0.5 * ((index - center) / 4.0) ** 2)
            for index in range(center + 25, center + 76):
                offset = index - (center + 50)
                values[index] += (
                    0.70 * math.exp(-0.5 * (offset / 9.0) ** 2)
                    + 0.25 * math.exp(-0.5 * ((offset - 8.0) / 3.0) ** 2)
                )

        true_consistency = analyzer._pulse_morphology_consistency(
            values, primary_indices, 100.0
        )
        false_dense_consistency = analyzer._pulse_morphology_consistency(
            values, dense_indices, 50.0
        )
        self.assertGreater(true_consistency, 0.95)
        self.assertLess(false_dense_consistency, true_consistency - 0.15)

    def test_close_sparse_and_dense_candidates_are_ambiguous(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)

        def estimate(bpm: float, score: float, beats: int) -> _BeatEstimate:
            return _BeatEstimate(
                bpm=bpm,
                periodicity=0.30,
                confidence=score,
                matched_peak_count=beats,
                strong_evidence=True,
                immediate_evidence=False,
                grid_confidence=0.75,
                spectral_peak_to_floor=12.0,
                spectral_concentration=0.30,
                period_agreement=0.05,
                morphology_consistency=0.80,
                relative_spectral_power=0.25,
            )

        sparse = estimate(50.0, 0.75, 6)
        dense = estimate(82.0, 0.71, 9)
        decision = analyzer._resolve_beat_density_ambiguity(
            [(sparse.confidence, sparse)],
            [(sparse.confidence, sparse), (dense.confidence, dense)],
        )
        self.assertIsNone(decision.estimate)
        self.assertEqual(decision.state, "ambiguous_period")

    def test_three_pulseless_windows_become_low_perfusion(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        states = []
        for second in range(10):
            first_sequence = second * SAMPLE_RATE_HZ
            analyzer.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=second,
                first_sample_sequence=first_sequence,
                first_sample_timestamp_ms=first_sequence * 10,
                ir_values=[100000.0] * SAMPLE_RATE_HZ,
            )
            states.append(analyzer.analyze().state)
        self.assertEqual(states[-1], "low_perfusion")

    def test_slow_baseline_ramp_does_not_masquerade_as_motion(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        sequence = 0

        def add_second(packet_sequence: int, dc: float) -> str:
            nonlocal sequence
            analyzer.add_samples(
                sample_rate_hz=SAMPLE_RATE_HZ,
                packet_sequence=packet_sequence,
                first_sample_sequence=sequence,
                first_sample_timestamp_ms=sequence * 10,
                ir_values=[
                    dc
                    + 25.0
                    * math.sin(2.0 * math.pi * 85.0 * sample / 6000.0)
                    for sample in range(sequence, sequence + SAMPLE_RATE_HZ)
                ],
            )
            sequence += SAMPLE_RATE_HZ
            return analyzer.analyze().state

        for second in range(8):
            add_second(second, 100000.0)
        ramp_states = [
            add_second(second, 100000.0 * (1.0 + 0.0025 * (second - 7)))
            for second in range(8, 16)
        ]
        self.assertNotIn("contact_unstable", ramp_states)

    def test_latest_capture_does_not_establish_startup_half_rate(self) -> None:
        replay = replay_wide_capture(
            Path(__file__).resolve().parent.parent
            / "data"
            / "sensor_data_20260725_185054.csv",
            {"person_01"},
        )["person_01"]
        valid = [result for _, result in replay if result.bpm is not None]

        self.assertTrue(valid)
        self.assertGreaterEqual(float(valid[0].bpm), 80.0)
        self.assertFalse(any(float(result.bpm) < 55.0 for result in valid))

    def test_latest_lossy_capture_recovers_when_old_gaps_leave_window(self) -> None:
        replay = replay_wide_capture(
            Path(__file__).resolve().parent.parent
            / "data"
            / "sensor_data_20260725_190615.csv",
            {"person_01"},
        )["person_01"]
        valid = [(timestamp, result) for timestamp, result in replay if result.bpm]

        self.assertTrue(valid)
        valid_index = next(
            index for index, (_, result) in enumerate(replay) if result.bpm
        )
        gap_free_indices = [
            index
            for index, (_, result) in enumerate(replay[:valid_index])
            if result.state in ("contact_unstable", "warming_up")
        ]
        usable_start = replay[gap_free_indices[-1]][0]
        startup_seconds = (valid[0][0] - usable_start).total_seconds()
        self.assertLessEqual(startup_seconds, 12.0)
        self.assertGreaterEqual(float(valid[0][1].bpm), 70.0)

    def test_production_default_rejects_implausible_40_to_50_bpm_track(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        minimum_lag, maximum_lag = analyzer._lag_bounds(SAMPLE_COUNT)

        self.assertEqual(analyzer.min_bpm, 55.0)
        self.assertLessEqual(maximum_lag, math.ceil(SAMPLE_RATE_HZ * 60.0 / 55.0))
        self.assertGreater(60.0 * SAMPLE_RATE_HZ / maximum_lag, 50.0)

    def test_latest_paired_capture_has_no_startup_half_rate(self) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "sensor_data_20260724_105113.csv"
        )
        if not path.exists():
            self.skipTest("local recorded PPG fixture is not available")
        replay = replay_wide_capture(path, {"person_01", "person_40"})
        finger = [
            (timestamp, result.bpm)
            for timestamp, result in replay["person_01"]
            if result.bpm is not None
        ]
        wrist = [
            (timestamp, result)
            for timestamp, result in replay["person_40"]
            if result.bpm is not None
        ]
        self.assertGreaterEqual(len(wrist), 65)
        errors = []
        for timestamp, result in wrist:
            _, reference_bpm = min(
                finger, key=lambda item: abs((item[0] - timestamp).total_seconds())
            )
            errors.append(abs(float(result.bpm) - float(reference_bpm)))
        self.assertGreaterEqual(
            sum(error <= 5.0 for error in errors), math.ceil(len(errors) * 0.95)
        )
        self.assertLessEqual(max(errors), 10.0)
        self.assertTrue(all(float(result.bpm) > 70.0 for _, result in wrist))

    def test_dual_finger_replay_does_not_switch_to_double_rate(self) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "sensor_data_20260724_124701.csv"
        )
        if not path.exists():
            self.skipTest("local recorded PPG fixture is not available")
        replay = replay_wide_capture(path, {"person_01", "person_40"})
        person_01 = [
            (timestamp, result.bpm)
            for timestamp, result in replay["person_01"]
            if result.bpm is not None
        ]
        person_40 = [
            (timestamp, result.bpm)
            for timestamp, result in replay["person_40"]
            if result.bpm is not None
        ]
        self.assertGreaterEqual(len(person_40), 8)
        self.assertTrue(all(float(bpm) < 120.0 for _, bpm in person_40))
        paired_errors = []
        for timestamp, bpm in person_40:
            reference_timestamp, reference_bpm = min(
                person_01,
                key=lambda item: abs((item[0] - timestamp).total_seconds()),
            )
            if abs((reference_timestamp - timestamp).total_seconds()) <= 1.5:
                paired_errors.append(abs(float(bpm) - float(reference_bpm)))
        self.assertTrue(paired_errors)
        self.assertLessEqual(statistics.median(paired_errors), 5.0)

    def test_ring_position_replay_starts_on_dense_strong_train(self) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "sensor_data_20260724_121458.csv"
        )
        if not path.exists():
            self.skipTest("local recorded PPG fixture is not available")
        replay = replay_wide_capture(path, {"person_40"})["person_40"]
        valid = [
            (timestamp, result)
            for timestamp, result in replay
            if result.bpm is not None
        ]
        self.assertTrue(valid)
        startup_seconds = (valid[0][0] - replay[0][0]).total_seconds()
        self.assertLessEqual(startup_seconds, 19.0)
        self.assertGreaterEqual(float(valid[0][1].bpm), 85.0)
        self.assertLessEqual(float(valid[0][1].bpm), 105.0)

    def test_loose_ring_contact_recovers_only_near_tracked_rate(self) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "sensor_data_20260724_123018.csv"
        )
        if not path.exists():
            self.skipTest("local recorded PPG fixture is not available")
        replay = replay_wide_capture(path, {"person_40"})["person_40"]
        loose_start = datetime.fromisoformat("2026-07-24T12:31:21+08:00")
        loose_end = datetime.fromisoformat("2026-07-24T12:32:49+08:00")
        loose_results = [
            result
            for timestamp, result in replay
            if loose_start <= timestamp <= loose_end
        ]
        valid = [result for result in loose_results if result.bpm is not None]
        self.assertGreaterEqual(len(valid), 12)
        self.assertTrue(all(80.0 <= float(result.bpm) <= 105.0 for result in valid))

    def test_lifted_wrist_capture_rejects_new_pressure_wave_bpm(self) -> None:
        path = (
            Path(__file__).resolve().parent.parent
            / "data"
            / "sensor_data_20260724_105408.csv"
        )
        if not path.exists():
            self.skipTest("local recorded PPG fixture is not available")
        replay = replay_wide_capture(path, {"person_40"})["person_40"]

        def interval(start: str, end: str) -> list:
            lower = datetime.fromisoformat(f"2026-07-24T{start}+08:00")
            upper = datetime.fromisoformat(f"2026-07-24T{end}.999+08:00")
            return [result for timestamp, result in replay if lower <= timestamp <= upper]

        first_tight = interval("10:54:23", "10:54:44")
        # The broad second contact interval contains another pressure
        # adjustment and its required re-warm.  The stable portion begins once
        # the eight-second baseline has settled again.
        second_tight = interval("10:55:11", "10:55:28")
        lifted = interval("10:55:29", "10:56:26")
        settling = interval("10:56:30", "10:56:53")
        self.assertGreaterEqual(
            sum(result.bpm is not None for result in first_tight),
            math.ceil(0.75 * len(first_tight)),
        )
        self.assertGreaterEqual(
            sum(result.bpm is not None for result in second_tight),
            math.ceil(0.75 * len(second_tight)),
        )
        self.assertTrue(all(result.bpm is None for result in lifted))
        self.assertFalse(
            any(
                result.state == "good"
                and result.bpm is not None
                and 43.0 <= result.bpm <= 50.0
                for result in settling
            )
        )


class AvailabilityRegressionTests(unittest.TestCase):
    """BPM availability must not be reduced by boundary bookkeeping."""

    @staticmethod
    def stream_blocks(
        analyzer: HeartRateAnalyzer,
        blocks: range,
        skip_blocks: set[int],
    ) -> list[tuple[int, object]]:
        """Feed 100 ms packets and analyze once per second like the collector."""

        results: list[tuple[int, object]] = []
        for block in blocks:
            if block not in skip_blocks:
                first_sequence = block * 10
                analyzer.add_samples(
                    sample_rate_hz=SAMPLE_RATE_HZ,
                    packet_sequence=block + 1,
                    first_sample_sequence=first_sequence,
                    first_sample_timestamp_ms=first_sequence * 10,
                    ir_values=[
                        sine_ir(sequence)
                        for sequence in range(first_sequence, first_sequence + 10)
                    ],
                )
            if block % 10 == 9 and analyzer.sample_count:
                results.append((block // 10 + 1, analyzer.analyze()))
        return results

    def test_steady_packet_loss_keeps_bpm_available(self) -> None:
        # Ten percent random packet loss must not blank BPM whenever a lost
        # packet slides across the window head: interior gaps of the same
        # size are interpolated.  Contact-step behavior has its own tests;
        # desensitizing it here keeps this a pure window-head regression.
        rng = random.Random(11)
        skip_blocks: set[int] = set()
        consecutive = 0
        for block in range(600):
            if rng.random() < 0.10 and consecutive < 4:
                skip_blocks.add(block)
                consecutive += 1
            else:
                consecutive = 0
        analyzer = HeartRateAnalyzer(
            SAMPLE_RATE_HZ,
            contact_step_ratio=0.98,
            contact_drift_ratio=0.97,
        )
        results = self.stream_blocks(analyzer, range(600), skip_blocks)
        steady = [result for second, result in results if second >= 13]
        self.assertTrue(steady)
        with_bpm = [result for result in steady if result.bpm is not None]
        self.assertGreaterEqual(len(with_bpm), math.ceil(0.95 * len(steady)))
        self.assertNotIn(
            "warming_up", {result.state for result in steady}
        )

    def test_short_dropout_keeps_track_and_recovers(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        before = self.stream_blocks(analyzer, range(300), set())
        self.assertIsNotNone(before[-1][1].bpm)
        tracked_before = analyzer._last_valid_bpm
        self.assertIsNotNone(tracked_before)
        # A 0.6 s dropout clears the sample window but not the rate track.
        after = self.stream_blocks(
            analyzer, range(300, 450), {300, 301, 302, 303, 304, 305}
        )
        self.assertEqual(analyzer._last_valid_bpm, tracked_before)
        recovered = [
            (second, result) for second, result in after if result.bpm is not None
        ]
        self.assertTrue(recovered)
        first_second, first_result = recovered[0]
        self.assertLessEqual(first_second, 40)
        self.assertAlmostEqual(float(first_result.bpm), TARGET_BPM, delta=4.0)

    def test_long_dropout_still_resets_track(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)
        self.stream_blocks(analyzer, range(300), set())
        self.assertIsNotNone(analyzer._last_valid_bpm)
        # Six seconds exceeds the track-keep limit and voids the history.
        self.stream_blocks(analyzer, range(300, 370), set(range(300, 360)))
        self.assertIsNone(analyzer._last_valid_bpm)

    def test_fractional_window_configuration_still_produces_bpm(self) -> None:
        # 8.05 s x 100 Hz rounds to 805 but ceils to 806; a shared window
        # constant must keep such configurations from never emitting a BPM.
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ, window_seconds=8.05)
        results = self.stream_blocks(analyzer, range(200), set())
        self.assertTrue(any(result.bpm is not None for _, result in results))

    def test_single_weak_window_does_not_restart_pending_count(self) -> None:
        analyzer = HeartRateAnalyzer(SAMPLE_RATE_HZ)

        def estimate(periodicity: float) -> _BeatEstimate:
            return _BeatEstimate(
                bpm=TARGET_BPM,
                periodicity=periodicity,
                confidence=0.55,
                matched_peak_count=6,
                strong_evidence=False,
                immediate_evidence=False,
                grid_confidence=0.60,
                spectral_peak_to_floor=8.0,
                spectral_concentration=0.20,
                period_agreement=0.05,
                morphology_consistency=0.75,
                relative_spectral_power=0.20,
            )

        consistent = estimate(0.30)
        weak = estimate(0.10)
        self.assertFalse(analyzer._gate_estimate(consistent, 100))
        self.assertFalse(analyzer._gate_estimate(consistent, 200))
        self.assertFalse(analyzer._gate_estimate(weak, 300))
        self.assertTrue(analyzer._gate_estimate(consistent, 400))
        self.assertIsNotNone(analyzer._last_valid_bpm)
        self.assertAlmostEqual(analyzer._last_valid_bpm, TARGET_BPM)


if __name__ == "__main__":
    unittest.main()
