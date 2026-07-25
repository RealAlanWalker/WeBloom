"""Gap-tolerant real-time heart-rate estimation for raw MAX30102 IR data.

The analyzer deliberately keeps interpolation inside the analysis window.  Raw
samples remain the source of truth and are never rewritten or synthesized for
storage.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import statistics
from typing import Any, Deque, Mapping, Sequence


# A gap longer than the interpolation limit clears the sample window, but the
# established heart-rate track survives dropouts up to this long: a brief
# radio outage does not mean the wearer's rate changed.
_TRACK_KEEP_SECONDS = 5.0


@dataclass(frozen=True)
class HeartRateResult:
    bpm: float | None
    state: str
    quality: float
    periodicity: float
    signal_rms: float
    sample_missing_rate: float
    max_gap_ms: int
    window_end_sample_seq: int | None
    window_end_timestamp_ms: int | None
    observed_samples: int
    missing_samples: int


@dataclass(frozen=True)
class _IrSample:
    sequence: int
    timestamp_ms: int
    ir: float


@dataclass(frozen=True)
class _DetectedPeak:
    index: int
    prominence: float


@dataclass(frozen=True)
class _BeatEstimate:
    bpm: float
    periodicity: float
    confidence: float
    matched_peak_count: int
    strong_evidence: bool
    immediate_evidence: bool
    grid_confidence: float
    spectral_peak_to_floor: float
    spectral_concentration: float
    period_agreement: float
    morphology_consistency: float
    relative_spectral_power: float


@dataclass(frozen=True)
class _GridMatch:
    confidence: float
    matched_indices: tuple[int, ...]
    morphology_consistency: float


@dataclass(frozen=True)
class _BeatDecision:
    estimate: _BeatEstimate | None
    state: str


class HeartRateAnalyzer:
    """Maintain one device's IR window and estimate heart rate.

    Packet sequence numbers are used only to reject duplicates/out-of-order
    packets and detect a likely device restart.  BPM continuity is determined
    exclusively from sample sequence numbers, so context packets cannot affect
    it.
    """

    def __init__(
        self,
        sample_rate_hz: int,
        *,
        window_seconds: float = 8.0,
        min_window_seconds: float = 4.0,
        min_bpm: float = 55.0,
        max_bpm: float = 200.0,
        max_interpolation_seconds: float = 0.500,
        good_missing_rate: float = 0.10,
        max_missing_rate: float = 0.20,
        min_periodicity: float = 0.35,
        min_signal_rms: float = 1.0,
        contact_ir_threshold: float = 10000.0,
        contact_step_ratio: float = 0.03,
        contact_drift_ratio: float = 0.005,
    ) -> None:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if not 0.0 <= good_missing_rate <= max_missing_rate < 1.0:
            raise ValueError("invalid missing-rate thresholds")
        if not 0.0 < min_bpm < max_bpm:
            raise ValueError("invalid BPM range")
        if contact_ir_threshold < 0.0:
            raise ValueError("contact_ir_threshold must be non-negative")
        if not 0.0 < contact_step_ratio < 1.0:
            raise ValueError("contact_step_ratio must be between zero and one")
        if not 0.0 < contact_drift_ratio < contact_step_ratio:
            raise ValueError("contact_drift_ratio must be below contact_step_ratio")
        self.window_seconds = window_seconds
        self.min_window_seconds = min_window_seconds
        self.min_bpm = min_bpm
        self.max_bpm = max_bpm
        self.max_interpolation_seconds = max_interpolation_seconds
        self.good_missing_rate = good_missing_rate
        self.max_missing_rate = max_missing_rate
        self.min_periodicity = min_periodicity
        self.min_signal_rms = min_signal_rms
        self.contact_ir_threshold = contact_ir_threshold
        self.contact_step_ratio = contact_step_ratio
        self.contact_drift_ratio = contact_drift_ratio

        self.sample_rate_hz = sample_rate_hz
        # Trimming and the BPM-window requirement must share one sample count,
        # otherwise round/ceil disagreement can make a full window unreachable.
        self._window_samples = int(round(window_seconds * sample_rate_hz))
        self._samples: Deque[_IrSample] = deque()
        self._last_packet_sequence: int | None = None
        self._last_sample_sequence: int | None = None
        self._warming_reason = "warming_up"
        self._quality_first_sequence: int | None = None
        self._quality_last_sequence: int | None = None
        self._quality_observed_samples = 0
        self._quality_max_gap_samples = 0
        self._last_valid_bpm: float | None = None
        self._pending_bpms: list[float] = []
        self._last_gated_sequence: int | None = None
        self._last_gate_accepted = False
        self._contact_locked = False
        self._contact_stable_start_sequence: int | None = None
        self._contact_last_block_start: int | None = None
        self._contact_last_block_median: float | None = None
        self._contact_last_settle_median: float | None = None
        self._contact_stable_block_count = 0
        self._no_pulse_window_count = 0

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    @property
    def last_packet_sequence(self) -> int | None:
        return self._last_packet_sequence

    def reset(self, sample_rate_hz: int | None = None, *, reason: str = "restarted") -> None:
        if sample_rate_hz is not None:
            if sample_rate_hz <= 0:
                raise ValueError("sample_rate_hz must be positive")
            self.sample_rate_hz = sample_rate_hz
            self._window_samples = int(round(self.window_seconds * sample_rate_hz))
        self._samples.clear()
        self._last_packet_sequence = None
        self._last_sample_sequence = None
        self._warming_reason = reason
        self._reset_rate_tracking()
        self._reset_quality_span()
        self._reset_contact_tracking()

    def add_packet(self, packet: Any) -> bool:
        """Add a raw PPG packet.

        Returns True when this packet caused a stream reset.  Objects carrying
        another ``packet_type`` are ignored, which makes accidental context
        dispatch harmless.
        """

        if isinstance(packet, Mapping):
            if packet.get("type", "raw_ppg_packet") != "raw_ppg_packet":
                return False
            samples = tuple(packet["samples"])
            ir_values = [sample[1] for sample in samples]
            return self.add_samples(
                sample_rate_hz=int(packet["sample_rate_hz"]),
                packet_sequence=int(packet["packet_seq"]),
                first_sample_sequence=int(packet["first_sample_seq"]),
                first_sample_timestamp_ms=int(packet["first_sample_timestamp_ms"]),
                ir_values=ir_values,
            )

        if getattr(packet, "packet_type", "raw_ppg_packet") != "raw_ppg_packet":
            return False
        return self.add_samples(
            sample_rate_hz=int(packet.sample_rate_hz),
            packet_sequence=int(packet.packet_sequence),
            first_sample_sequence=int(packet.first_sample_sequence),
            first_sample_timestamp_ms=int(packet.first_sample_timestamp_ms),
            ir_values=[sample.ir for sample in packet.samples],
        )

    def add_samples(
        self,
        *,
        sample_rate_hz: int,
        packet_sequence: int,
        first_sample_sequence: int,
        first_sample_timestamp_ms: int,
        ir_values: Sequence[float],
    ) -> bool:
        """Add one contiguous raw IR packet without depending on a packet class."""

        if not ir_values:
            return False

        did_reset = False
        if sample_rate_hz != self.sample_rate_hz:
            self.reset(sample_rate_hz, reason="sample_rate_changed")
            did_reset = True

        if self._last_packet_sequence is not None and packet_sequence <= self._last_packet_sequence:
            # A sequence returning close to zero after a well-established stream
            # is a restart.  Smaller ordinary reversals are stale radio packets.
            likely_restart = (
                packet_sequence < self._last_packet_sequence
                and self._last_sample_sequence is not None
                and first_sample_sequence <= self._last_sample_sequence
                and (
                    packet_sequence <= 5
                    or self._last_packet_sequence - packet_sequence > 100
                )
            )
            if likely_restart:
                self.reset(sample_rate_hz, reason="restarted")
                did_reset = True
            else:
                return did_reset

        new_samples: list[_IrSample] = []
        for index, ir_value in enumerate(ir_values):
            sequence = first_sample_sequence + index
            if self._last_sample_sequence is not None and sequence <= self._last_sample_sequence:
                continue
            timestamp_ms = int(
                round(first_sample_timestamp_ms + index * 1000.0 / sample_rate_hz)
            )
            new_samples.append(_IrSample(sequence, timestamp_ms, float(ir_value)))

        if not new_samples:
            return did_reset

        if self._last_sample_sequence is not None:
            missing_before_packet = new_samples[0].sequence - self._last_sample_sequence - 1
            max_gap_samples = int(round(self.max_interpolation_seconds * sample_rate_hz))
            if missing_before_packet > max_gap_samples:
                self._samples.clear()
                self._warming_reason = "long_gap"
                self._reset_quality_span()
                self._reset_contact_tracking()
                track_keep_samples = int(round(_TRACK_KEEP_SECONDS * sample_rate_hz))
                if missing_before_packet > track_keep_samples:
                    self._reset_rate_tracking()
                else:
                    # Keep the established track across a short dropout so a
                    # single consistent window can resume output once the
                    # window refills.  Pending evidence is stale either way.
                    self._pending_bpms = []
                    self._last_gated_sequence = None
                    self._last_gate_accepted = False
                    self._no_pulse_window_count = 0
            else:
                self._quality_max_gap_samples = max(
                    self._quality_max_gap_samples, missing_before_packet
                )

        self._samples.extend(new_samples)
        if self._quality_first_sequence is None:
            self._quality_first_sequence = new_samples[0].sequence
        self._quality_last_sequence = new_samples[-1].sequence
        self._quality_observed_samples += len(new_samples)
        self._last_packet_sequence = packet_sequence
        self._last_sample_sequence = new_samples[-1].sequence
        self._trim_window()
        return did_reset

    def analyze(self) -> HeartRateResult:
        if not self._samples:
            return self._empty_result(self._warming_reason)

        first = self._samples[0]
        last = self._samples[-1]
        full_span = last.sequence - first.sequence + 1
        missing_samples = max(0, full_span - len(self._samples))
        full_missing_rate = missing_samples / full_span
        full_max_gap_samples = self._max_gap_samples(list(self._samples))

        contact_state, stable_first_sequence = self._contact_window_start()
        if contact_state == "no_contact":
            self._reset_rate_tracking()
            return self._result(
                state="no_contact",
                missing_rate=full_missing_rate,
                max_gap_ms=int(
                    round(full_max_gap_samples * 1000.0 / self.sample_rate_hz)
                ),
                observed_samples=len(self._samples),
                missing_samples=missing_samples,
            )

        unstable_continuity = (
            contact_state == "contact_unstable"
            and self._last_valid_bpm is not None
        )
        analysis_samples = [
            sample for sample in self._samples
            if unstable_continuity or sample.sequence >= stable_first_sequence
        ]
        first = analysis_samples[0]
        span = last.sequence - first.sequence + 1
        min_samples = int(math.ceil(self.min_window_seconds * self.sample_rate_hz))
        window_missing_samples = max(0, span - len(analysis_samples))
        missing_rate = window_missing_samples / span if span else 0.0
        max_gap_samples = self._max_gap_samples(analysis_samples)
        max_gap_ms = int(round(max_gap_samples * 1000.0 / self.sample_rate_hz))

        if (
            contact_state == "contact_unstable"
            and self._warming_reason == "warming_up"
            and not unstable_continuity
        ):
            self._reset_rate_tracking()
            return self._result(
                state="contact_unstable",
                missing_rate=missing_rate,
                max_gap_ms=max_gap_ms,
                observed_samples=len(analysis_samples),
                missing_samples=missing_samples,
            )

        if span < min_samples:
            state = (
                self._warming_reason
                if self._warming_reason != "warming_up"
                else (
                    "contact_unstable"
                    if contact_state == "contact_unstable"
                    else self._warming_reason
                )
            )
            if state == "contact_unstable":
                self._reset_rate_tracking()
            return self._result(
                state=state,
                missing_rate=missing_rate,
                max_gap_ms=max_gap_ms,
                observed_samples=len(analysis_samples),
                missing_samples=missing_samples,
            )

        self._warming_reason = "warming_up"
        if max_gap_samples > int(round(self.max_interpolation_seconds * self.sample_rate_hz)):
            return self._result(
                state="long_gap",
                missing_rate=missing_rate,
                max_gap_ms=max_gap_ms,
                observed_samples=len(analysis_samples),
                missing_samples=missing_samples,
            )

        if missing_rate > self.max_missing_rate:
            return self._result(
                state="too_many_gaps",
                missing_rate=missing_rate,
                max_gap_ms=max_gap_ms,
                observed_samples=len(analysis_samples),
                missing_samples=missing_samples,
            )

        values = self._interpolated_values(first.sequence, span)
        filtered = self._bandpass(values)
        evidence_filtered = self._evidence_bandpass(values)
        signal_rms = math.sqrt(
            sum(value * value for value in evidence_filtered) / len(evidence_filtered)
        )
        if signal_rms < self.min_signal_rms:
            is_new_window = self._last_gated_sequence != last.sequence
            self._gate_estimate(None, last.sequence)
            if is_new_window:
                self._no_pulse_window_count += 1
            return self._result(
                state=(
                    "low_perfusion"
                    if self._no_pulse_window_count >= 3
                    else "low_signal"
                ),
                signal_rms=signal_rms,
                missing_rate=missing_rate,
                max_gap_ms=max_gap_ms,
                observed_samples=len(analysis_samples),
                missing_samples=missing_samples,
            )

        # Contact and signal diagnostics become useful after four seconds; a
        # BPM estimate wants the complete configured window.  The window head
        # may still erode by up to the interpolation limit when the boundary
        # sample itself was lost -- rejecting those windows would blank BPM at
        # the packet-loss rate even though equal interior gaps are tolerated.
        head_allowance = int(
            round(self.max_interpolation_seconds * self.sample_rate_hz)
        )
        if span < self._window_samples - head_allowance:
            return self._result(
                state="warming_up",
                signal_rms=signal_rms,
                missing_rate=missing_rate,
                max_gap_ms=max_gap_ms,
                observed_samples=len(analysis_samples),
                missing_samples=missing_samples,
            )

        decision = self._estimate_beat_decision(filtered, evidence_filtered)
        estimate = decision.estimate
        if estimate is None:
            is_new_window = self._last_gated_sequence != last.sequence
            self._gate_estimate(None, last.sequence)
            if decision.state == "ambiguous_period":
                self._no_pulse_window_count = 0
            elif is_new_window:
                self._no_pulse_window_count += 1
            _, periodicity = self._best_autocorrelation(evidence_filtered)
            quality = max(0.0, periodicity) * (1.0 - missing_rate) * 0.5
            return self._result(
                state=(
                    decision.state
                    if decision.state == "ambiguous_period"
                    else (
                        "low_perfusion"
                        if self._no_pulse_window_count >= 3
                        else "low_periodicity"
                    )
                ),
                quality=quality,
                periodicity=max(0.0, periodicity),
                signal_rms=signal_rms,
                missing_rate=missing_rate,
                max_gap_ms=max_gap_ms,
                observed_samples=len(analysis_samples),
                missing_samples=missing_samples,
            )

        self._no_pulse_window_count = 0
        bpm = estimate.bpm
        periodicity = estimate.periodicity
        quality = estimate.confidence * (1.0 - missing_rate)
        if unstable_continuity:
            assert self._last_valid_bpm is not None
            deviation = abs(bpm - self._last_valid_bpm) / self._last_valid_bpm
            if not estimate.strong_evidence or deviation > 0.09:
                self._pending_bpms = []
                return self._result(
                    state="contact_unstable",
                    quality=quality * 0.5,
                    periodicity=max(0.0, periodicity),
                    signal_rms=signal_rms,
                    missing_rate=missing_rate,
                    max_gap_ms=max_gap_ms,
                    observed_samples=len(analysis_samples),
                    missing_samples=missing_samples,
                )
        if not self._gate_estimate(estimate, last.sequence):
            return self._result(
                state=(
                    "low_periodicity"
                    if decision.state == "spectral_dominant"
                    else (
                        "ambiguous_period"
                        if (
                        self._last_valid_bpm is None
                        and estimate.periodicity < 0.15
                        and estimate.matched_peak_count < 9
                        )
                        else "low_periodicity"
                    )
                ),
                quality=quality * 0.5,
                periodicity=max(0.0, periodicity),
                signal_rms=signal_rms,
                missing_rate=missing_rate,
                max_gap_ms=max_gap_ms,
                observed_samples=len(analysis_samples),
                missing_samples=missing_samples,
            )

        state = (
            "good"
            if (
                not unstable_continuity
                and estimate.strong_evidence
                and missing_rate <= self.good_missing_rate
            )
            else "degraded"
        )
        return self._result(
            bpm=bpm,
            state=state,
            quality=quality,
            periodicity=periodicity,
            signal_rms=signal_rms,
            missing_rate=missing_rate,
            max_gap_ms=max_gap_ms,
            observed_samples=len(analysis_samples),
            missing_samples=missing_samples,
        )

    def _trim_window(self) -> None:
        if not self._samples:
            return
        minimum_sequence = self._samples[-1].sequence - self._window_samples + 1
        while self._samples and self._samples[0].sequence < minimum_sequence:
            self._samples.popleft()

    def _reset_quality_span(self) -> None:
        self._quality_first_sequence = None
        self._quality_last_sequence = None
        self._quality_observed_samples = 0
        self._quality_max_gap_samples = 0

    def _reset_rate_tracking(self) -> None:
        self._last_valid_bpm = None
        self._pending_bpms = []
        self._last_gated_sequence = None
        self._last_gate_accepted = False
        self._no_pulse_window_count = 0

    def _record_pending_bpm(self, bpm: float, required: int) -> bool:
        if self._pending_bpms:
            center = float(statistics.median(self._pending_bpms))
            if abs(bpm - center) / center > 0.08:
                self._pending_bpms = [bpm]
            else:
                self._pending_bpms.append(bpm)
        else:
            self._pending_bpms = [bpm]
        if len(self._pending_bpms) < required:
            return False
        self._last_valid_bpm = float(statistics.median(self._pending_bpms[-required:]))
        self._pending_bpms = []
        return True

    def _gate_estimate(
        self,
        estimate: _BeatEstimate | None,
        window_end_sequence: int,
    ) -> bool:
        """Apply short evidence persistence without synthesizing a BPM."""

        if self._last_gated_sequence == window_end_sequence:
            return self._last_gate_accepted
        self._last_gated_sequence = window_end_sequence
        self._last_gate_accepted = False

        if estimate is None:
            self._pending_bpms = []
            return False

        bpm = estimate.bpm
        if self._last_valid_bpm is None:
            # A complete eight-second window can itself contain more than
            # three independent, repeatable beats.  Exceptionally strong,
            # unambiguous evidence may therefore establish the first track in
            # one analysis call; weaker windows still need three consecutive
            # one-second decisions.  Ambiguity is resolved before this gate.
            immediate_repeatability = (
                estimate.matched_peak_count >= 9
                or estimate.periodicity >= 0.60
            )
            dense_strong_train = (
                estimate.strong_evidence
                and estimate.confidence >= 0.70
                and estimate.matched_peak_count >= 10
            )
            if (
                estimate.immediate_evidence and immediate_repeatability
            ) or dense_strong_train:
                self._last_valid_bpm = bpm
                self._pending_bpms = []
                self._last_gate_accepted = True
                return True
            # With no trusted history, a sparse optical wave must either
            # repeat at its own period or contain a denser train of matched
            # events.  This protects every BPM band uniformly: genuine slow
            # rhythms normally have the former, faster weak rhythms the latter.
            # One weak window must not restart the three-consecutive count;
            # already accumulated consistent evidence stays pending.
            if estimate.periodicity < 0.15 and estimate.matched_peak_count < 9:
                return False
            accepted = self._record_pending_bpm(bpm, required=3)
            self._last_gate_accepted = accepted
            return accepted

        deviation = abs(bpm - self._last_valid_bpm) / self._last_valid_bpm
        # One-second updates should not ratchet through a sequence of merely
        # adjacent artifact rates.  Larger physiological changes are still
        # allowed after three mutually consistent strong windows.
        if deviation <= 0.09:
            self._last_valid_bpm = 0.75 * self._last_valid_bpm + 0.25 * bpm
            self._pending_bpms = []
            self._last_gate_accepted = True
            return True

        if estimate.strong_evidence and self._record_pending_bpm(bpm, required=3):
            self._last_gate_accepted = True
            return True
        if not estimate.strong_evidence:
            self._pending_bpms = []
        return False

    def _reset_contact_tracking(self) -> None:
        self._contact_locked = False
        self._contact_stable_start_sequence = None
        self._contact_last_block_start = None
        self._contact_last_block_median = None
        self._contact_last_settle_median = None
        self._contact_stable_block_count = 0

    def _contact_window_start(self) -> tuple[str, int]:
        """Return contact state and first sequence safe for pulse analysis.

        Contact checks use one-second raw-IR medians.  This deliberately stays
        outside the filtered BPM path: it identifies optical coupling changes,
        while the original samples remain untouched in the collector output.
        """

        samples = list(self._samples)
        first_sequence = samples[0].sequence
        last_sequence = samples[-1].sequence
        block_size = max(1, self.sample_rate_hz)
        recent_start = last_sequence - block_size + 1
        recent_values = [sample.ir for sample in samples if sample.sequence >= recent_start]
        if recent_values and statistics.median(recent_values) < self.contact_ir_threshold:
            self._reset_contact_tracking()
            return "no_contact", first_sequence

        # A removal or pressure step can occur inside the newest incomplete
        # one-second block.  Quarter-second medians catch it without waiting
        # for the ordinary one-second contact state machine to advance.
        quarter_size = max(1, block_size // 4)
        quarter_medians: list[tuple[int, float]] = []
        quarter_span_start = last_sequence - 4 * quarter_size + 1
        for offset in range(4):
            start = quarter_span_start + offset * quarter_size
            end = start + quarter_size
            values = [
                sample.ir for sample in samples if start <= sample.sequence < end
            ]
            if len(values) >= max(1, quarter_size // 2):
                quarter_medians.append((start, float(statistics.median(values))))
        if quarter_medians and quarter_medians[-1][1] < self.contact_ir_threshold:
            self._reset_contact_tracking()
            return "no_contact", first_sequence
        if self._contact_locked and len(quarter_medians) >= 2:
            for (_, previous_median), (current_start, current_median) in zip(
                quarter_medians, quarter_medians[1:]
            ):
                scale = max(abs(previous_median), self.contact_ir_threshold, 1.0)
                if abs(current_median - previous_median) / scale > self.contact_step_ratio:
                    self._contact_locked = False
                    self._contact_stable_start_sequence = current_start
                    self._contact_stable_block_count = 0
                    self._contact_last_settle_median = None
                    self._contact_last_block_start = (
                        last_sequence // block_size
                    ) * block_size
                    self._contact_last_block_median = current_median
                    return "contact_unstable", current_start

        blocks: list[tuple[int, float]] = []
        # Short radio gaps are filled only in this in-memory view so packet
        # loss cannot masquerade as optical contact motion.  Stored raw PPG is
        # never changed.
        contact_values = self._interpolated_values(
            first_sequence, last_sequence - first_sequence + 1
        )
        block_start = (first_sequence // block_size) * block_size
        while block_start <= last_sequence:
            block_end = block_start + block_size
            covered_start = max(first_sequence, block_start)
            covered_end = min(last_sequence + 1, block_end)
            block_values = contact_values[
                covered_start - first_sequence:covered_end - first_sequence
            ]
            if len(block_values) >= max(1, int(round(block_size * 0.75))):
                blocks.append((block_start, float(statistics.median(block_values))))
            block_start = block_end

        required_blocks = max(2, int(math.ceil(self.min_window_seconds)))
        for current_start, current_median in blocks:
            if (
                self._contact_last_block_start is not None
                and current_start <= self._contact_last_block_start
            ):
                continue

            previous_median = self._contact_last_block_median
            above_contact = current_median >= self.contact_ir_threshold
            previous_above_contact = (
                previous_median is not None
                and previous_median >= self.contact_ir_threshold
            )
            if not above_contact:
                self._contact_locked = False
                self._contact_stable_start_sequence = None
                self._contact_stable_block_count = 0
                self._contact_last_settle_median = None
            elif not previous_above_contact:
                self._contact_locked = False
                self._contact_stable_start_sequence = current_start
                self._contact_stable_block_count = 1
                self._contact_last_settle_median = None
            else:
                scale = max(abs(previous_median), self.contact_ir_threshold, 1.0)
                change_ratio = abs(current_median - previous_median) / scale
                if change_ratio > self.contact_step_ratio:
                    self._contact_locked = False
                    self._contact_stable_start_sequence = current_start
                    self._contact_stable_block_count = 1
                    self._contact_last_settle_median = None
                elif not self._contact_locked:
                    # Averaging adjacent one-second medians removes the
                    # cardiac-phase bias of a block containing a non-integer
                    # number of beats.  Abrupt steps above are still detected
                    # from the unsmoothed medians.
                    settle_median = (previous_median + current_median) / 2.0
                    previous_settle_median = self._contact_last_settle_median
                    settle_scale = max(
                        abs(previous_settle_median or settle_median),
                        self.contact_ir_threshold,
                        1.0,
                    )
                    settle_change_ratio = (
                        abs(settle_median - previous_settle_median) / settle_scale
                        if previous_settle_median is not None
                        else 0.0
                    )
                    if settle_change_ratio < self.contact_drift_ratio:
                        self._contact_stable_block_count += 1
                    else:
                        self._contact_stable_start_sequence = current_start
                        self._contact_stable_block_count = 1
                    self._contact_last_settle_median = settle_median
                    if self._contact_stable_block_count >= required_blocks:
                        self._contact_locked = True
                else:
                    self._contact_last_settle_median = (
                        previous_median + current_median
                    ) / 2.0

            self._contact_last_block_start = current_start
            self._contact_last_block_median = current_median

        stable_first_sequence = max(
            first_sequence,
            self._contact_stable_start_sequence or first_sequence,
        )
        if not self._contact_locked:
            return "contact_unstable", stable_first_sequence
        stable_span = last_sequence - stable_first_sequence + 1
        minimum_span = int(math.ceil(self.min_window_seconds * self.sample_rate_hz))
        state = "contact_unstable" if stable_span < minimum_span else "stable"
        return state, stable_first_sequence

    @staticmethod
    def _max_gap_samples(samples: Sequence[_IrSample]) -> int:
        maximum = 0
        previous: int | None = None
        for sample in samples:
            if previous is not None:
                maximum = max(maximum, sample.sequence - previous - 1)
            previous = sample.sequence
        return maximum

    def _interpolated_values(self, first_sequence: int, span: int) -> list[float]:
        values: list[float | None] = [None] * span
        for sample in self._samples:
            if not first_sequence <= sample.sequence < first_sequence + span:
                continue
            values[sample.sequence - first_sequence] = sample.ir

        previous_index = 0
        while previous_index < span:
            if values[previous_index] is None:
                raise RuntimeError("analysis window must start with a real sample")
            next_index = previous_index + 1
            while next_index < span and values[next_index] is None:
                next_index += 1
            if next_index >= span:
                break
            if next_index > previous_index + 1:
                start = float(values[previous_index])
                end = float(values[next_index])
                width = next_index - previous_index
                for offset in range(1, width):
                    values[previous_index + offset] = start + (end - start) * offset / width
            previous_index = next_index

        if any(value is None for value in values):
            raise RuntimeError("analysis window must end with a real sample")
        return [float(value) for value in values]

    def _detrend(self, values: list[float]) -> list[float]:
        # The baseline window follows the slowest allowed pulse period instead
        # of being tuned to a particular nominal heart rate.
        radius = max(
            1,
            int(round(self.sample_rate_hz * 60.0 / self.min_bpm / 2.0)),
        )
        prefix = [0.0]
        for value in values:
            prefix.append(prefix[-1] + value)
        detrended: list[float] = []
        for index, value in enumerate(values):
            left = max(0, index - radius)
            right = min(len(values), index + radius + 1)
            local_mean = (prefix[right] - prefix[left]) / (right - left)
            detrended.append(value - local_mean)
        mean = sum(detrended) / len(detrended)
        return [value - mean for value in detrended]

    def _bandpass(self, values: list[float]) -> list[float]:
        """Preserve pulse morphology with a dependency-free band-pass."""

        detrended = self._detrend(values)
        smoothing_width = max(
            1,
            int(round(self.sample_rate_hz * 60.0 / self.max_bpm / 5.0)),
        )
        if smoothing_width <= 1:
            return detrended
        if smoothing_width % 2 == 0:
            smoothing_width += 1
        radius = smoothing_width // 2
        prefix = [0.0]
        for value in detrended:
            prefix.append(prefix[-1] + value)
        filtered: list[float] = []
        for index in range(len(detrended)):
            left = max(0, index - radius)
            right = min(len(detrended), index + radius + 1)
            filtered.append((prefix[right] - prefix[left]) / (right - left))
        mean = sum(filtered) / len(filtered)
        return [value - mean for value in filtered]

    def _evidence_bandpass(self, values: list[float]) -> list[float]:
        """Apply a zero-phase dependency-free biquad PPG band-pass.

        Reflection padding prevents the slow baseline at either edge from
        dominating autocorrelation.  Cutoffs follow the configured BPM range
        rather than a preferred nominal heart rate.
        """

        if len(values) < 3:
            return [0.0 for _ in values]
        center = float(statistics.median(values))
        centered = [value - center for value in values]
        pad = min(
            len(centered) - 1,
            max(6, int(round(self.sample_rate_hz * 60.0 / self.min_bpm))),
        )
        padded = (
            centered[1:pad + 1][::-1]
            + centered
            + centered[-pad - 1:-1][::-1]
        )
        high_cutoff_hz = 0.75 * self.min_bpm / 60.0
        low_cutoff_hz = 1.25 * self.max_bpm / 60.0
        high = self._biquad_coefficients("highpass", high_cutoff_hz)
        low = self._biquad_coefficients("lowpass", low_cutoff_hz)

        filtered = self._apply_biquad(padded, high)
        filtered = self._apply_biquad(filtered, low)
        filtered.reverse()
        filtered = self._apply_biquad(filtered, high)
        filtered = self._apply_biquad(filtered, low)
        filtered.reverse()
        filtered = filtered[pad:pad + len(centered)]
        mean = sum(filtered) / len(filtered)
        return [value - mean for value in filtered]

    def _biquad_coefficients(
        self, filter_type: str, cutoff_hz: float
    ) -> tuple[float, float, float, float, float]:
        cutoff_hz = min(
            self.sample_rate_hz * 0.45,
            max(self.sample_rate_hz * 0.001, cutoff_hz),
        )
        omega = 2.0 * math.pi * cutoff_hz / self.sample_rate_hz
        cosine = math.cos(omega)
        alpha = math.sin(omega) / math.sqrt(2.0)
        if filter_type == "lowpass":
            b0 = (1.0 - cosine) / 2.0
            b1 = 1.0 - cosine
            b2 = b0
        elif filter_type == "highpass":
            b0 = (1.0 + cosine) / 2.0
            b1 = -(1.0 + cosine)
            b2 = b0
        else:
            raise ValueError("unsupported biquad filter type")
        a0 = 1.0 + alpha
        a1 = -2.0 * cosine
        a2 = 1.0 - alpha
        return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0

    @staticmethod
    def _apply_biquad(
        values: Sequence[float],
        coefficients: tuple[float, float, float, float, float],
    ) -> list[float]:
        b0, b1, b2, a1, a2 = coefficients
        input_one = 0.0
        input_two = 0.0
        output_one = 0.0
        output_two = 0.0
        output: list[float] = []
        for value in values:
            filtered = (
                b0 * value
                + b1 * input_one
                + b2 * input_two
                - a1 * output_one
                - a2 * output_two
            )
            input_two = input_one
            input_one = value
            output_two = output_one
            output_one = filtered
            output.append(filtered)
        return output

    def _estimate_beats(
        self,
        values: list[float],
        evidence_values: list[float] | None = None,
    ) -> _BeatEstimate | None:
        """Compatibility wrapper returning only an unambiguous estimate."""

        return self._estimate_beat_decision(values, evidence_values).estimate

    def _estimate_beat_decision(
        self,
        values: list[float],
        evidence_values: list[float] | None = None,
    ) -> _BeatDecision:
        """Estimate BPM from a matched sequence of individual pulse peaks.

        Autocorrelation and spectral power only nominate/score periods.  The
        returned BPM is computed from the median interval between matched pulse
        peaks, so a strong two-beat autocorrelation peak cannot halve the rate.
        """

        minimum_lag, maximum_lag = self._lag_bounds(len(values))
        if maximum_lag < minimum_lag:
            return _BeatDecision(None, "low_periodicity")
        if evidence_values is None:
            evidence_values = values

        autocorrelation = {
            lag: self._normalized_correlation(evidence_values, lag)
            for lag in range(minimum_lag, maximum_lag + 1)
        }
        spectral_power = self._spectral_profile(
            evidence_values, minimum_lag, maximum_lag
        )
        spectral_bins = self._uniform_spectral_bins(evidence_values)

        positive_peaks = self._detect_peaks(values, minimum_lag, maximum_lag)
        negative_peaks = self._detect_peaks(
            [-value for value in values], minimum_lag, maximum_lag
        )
        peak_sets = [
            (values, positive_peaks),
            ([-value for value in values], negative_peaks),
        ]

        candidate_lags: set[int] = set()
        candidate_lags.update(self._profile_peaks(autocorrelation, limit=8))
        candidate_lags.update(self._profile_peaks(spectral_power, limit=8))
        for _, peaks in peak_sets:
            for left, right in zip(peaks, peaks[1:]):
                difference = right.index - left.index
                if minimum_lag <= difference <= maximum_lag:
                    candidate_lags.add(difference)
                for divisor in (2, 3):
                    divided = int(round(difference / divisor))
                    if minimum_lag <= divided <= maximum_lag:
                        candidate_lags.add(divided)

        expanded_candidates: set[int] = set()
        for lag in candidate_lags:
            for offset in (-2, -1, 0, 1, 2):
                candidate = lag + offset
                if minimum_lag <= candidate <= maximum_lag:
                    expanded_candidates.add(candidate)

        accepted_estimates: list[tuple[float, _BeatEstimate]] = []
        plausible_estimates: list[tuple[float, _BeatEstimate]] = []
        for oriented_values, peaks in peak_sets:
            for lag in sorted(expanded_candidates):
                match = self._match_peak_grid(oriented_values, peaks, float(lag))
                if match is None:
                    continue
                grid_confidence = match.confidence
                matched_indices = list(match.matched_indices)
                morphology_consistency = match.morphology_consistency

                intervals: list[float] = []
                for left, right in zip(matched_indices, matched_indices[1:]):
                    difference = float(right - left)
                    skipped_beats = max(1, int(round(difference / lag)))
                    intervals.append(difference / skipped_beats)
                if len(intervals) < 4:
                    continue
                median_interval = float(statistics.median(intervals))
                if median_interval <= 0.0:
                    continue
                candidate_frequency = self.sample_rate_hz / lag
                (
                    spectral_peak_to_floor,
                    spectral_concentration,
                    spectral_frequency,
                    relative_spectral_power,
                ) = self._candidate_spectral_evidence(
                    spectral_bins, candidate_frequency
                )
                spectral_interval = self.sample_rate_hz / spectral_frequency
                period_agreement = max(
                    abs(median_interval - lag) / lag,
                    abs(spectral_interval - lag) / lag,
                )

                correlation_radius = max(2, int(round(lag * 0.10)))
                correlation_left = max(minimum_lag, lag - correlation_radius)
                correlation_right = min(maximum_lag, lag + correlation_radius)
                periodicity = max(
                    0.0,
                    max(
                        autocorrelation.get(candidate, 0.0)
                        for candidate in range(correlation_left, correlation_right + 1)
                    ),
                )
                spectral_score = (
                    0.40 * min(1.0, spectral_peak_to_floor / 20.0)
                    + 0.60 * min(1.0, spectral_concentration / 0.50)
                )
                autocorrelation_score = min(1.0, periodicity / 0.80)
                agreement_score = max(0.0, 1.0 - period_agreement / 0.15)
                confidence = (
                    0.62 * grid_confidence
                    + 0.16 * spectral_score
                    + 0.10 * morphology_consistency
                    + 0.07 * agreement_score
                    + 0.05 * autocorrelation_score
                )
                balanced_evidence = (
                    grid_confidence >= 0.60
                    and morphology_consistency >= 0.65
                    and spectral_peak_to_floor >= 6.0
                    and spectral_concentration >= 0.15
                    and period_agreement <= 0.10
                    and confidence >= 0.60
                    and (
                        spectral_peak_to_floor >= 10.0
                        or periodicity >= 0.25
                    )
                )
                # A concentrated slow optical wave can look spectrally clean
                # while containing too few repeated beats to distinguish it
                # from pressure/baseline motion.  Strong evidence therefore
                # needs either actual autocorrelation support or a sufficiently
                # dense matched beat train.  Genuine low rates normally get
                # the former; higher rates can get the latter.
                repeatable_beat_evidence = (
                    periodicity >= 0.25 or len(matched_indices) >= 8
                )
                strong_evidence = (
                    balanced_evidence
                    and confidence >= 0.70
                    and grid_confidence >= 0.70
                    and repeatable_beat_evidence
                )
                immediate_evidence = strong_evidence and confidence >= 0.80
                bpm = 60.0 * self.sample_rate_hz / median_interval
                if not self.min_bpm * 0.95 <= bpm <= self.max_bpm * 1.05:
                    continue
                bpm = min(self.max_bpm, max(self.min_bpm, bpm))

                estimate = _BeatEstimate(
                    bpm=bpm,
                    periodicity=periodicity,
                    confidence=min(1.0, max(0.0, confidence)),
                    matched_peak_count=len(matched_indices),
                    strong_evidence=strong_evidence,
                    immediate_evidence=immediate_evidence,
                    grid_confidence=grid_confidence,
                    spectral_peak_to_floor=spectral_peak_to_floor,
                    spectral_concentration=spectral_concentration,
                    period_agreement=period_agreement,
                    morphology_consistency=morphology_consistency,
                    relative_spectral_power=relative_spectral_power,
                )
                ranking = confidence
                plausible_estimates.append((ranking, estimate))
                if balanced_evidence:
                    accepted_estimates.append((ranking, estimate))

        if not accepted_estimates:
            if self._last_valid_bpm is not None:
                tracked_plausible = [
                    (ranking, estimate)
                    for ranking, estimate in self._deduplicate_estimates(
                        plausible_estimates
                    )
                    if abs(estimate.bpm - self._last_valid_bpm)
                    / self._last_valid_bpm
                    <= 0.09
                    and estimate.grid_confidence >= 0.55
                    and estimate.morphology_consistency >= 0.65
                    and estimate.relative_spectral_power >= 0.10
                    and estimate.period_agreement <= 0.12
                    and estimate.confidence >= 0.60
                ]
                if tracked_plausible:
                    tracked_plausible.sort(
                        key=lambda item: item[0], reverse=True
                    )
                    return _BeatDecision(
                        tracked_plausible[0][1], "estimated"
                    )
            return _BeatDecision(None, "low_periodicity")
        accepted_estimates = self._deduplicate_estimates(accepted_estimates)
        plausible_estimates = self._deduplicate_estimates(plausible_estimates)
        if self._last_valid_bpm is not None:
            tracked_pool = [
                (ranking, estimate)
                for ranking, estimate in plausible_estimates
                if abs(estimate.bpm - self._last_valid_bpm)
                / self._last_valid_bpm
                <= 0.09
                and estimate.grid_confidence >= 0.55
                and estimate.morphology_consistency >= 0.65
                and estimate.relative_spectral_power >= 0.10
                and estimate.period_agreement <= 0.12
                and estimate.confidence >= 0.60
            ]
            selected_bpm = max(
                (estimate.bpm for _, estimate in accepted_estimates),
                default=0.0,
            )
            if (
                tracked_pool
                and selected_bpm >= 1.8 * self._last_valid_bpm
            ):
                tracked_pool.sort(key=lambda item: item[0], reverse=True)
                return _BeatDecision(tracked_pool[0][1], "estimated")
        return self._resolve_beat_density_ambiguity(
            accepted_estimates, plausible_estimates
        )

    @staticmethod
    def _deduplicate_estimates(
        estimates: Sequence[tuple[float, _BeatEstimate]],
    ) -> list[tuple[float, _BeatEstimate]]:
        """Keep one representative for near-identical period candidates."""

        unique: list[tuple[float, _BeatEstimate]] = []
        for ranking, estimate in sorted(estimates, key=lambda item: item[0], reverse=True):
            if any(
                abs(estimate.bpm - kept.bpm) / kept.bpm <= 0.06
                for _, kept in unique
            ):
                continue
            unique.append((ranking, estimate))
        return unique

    def _resolve_beat_density_ambiguity(
        self,
        accepted_estimates: Sequence[tuple[float, _BeatEstimate]],
        plausible_estimates: Sequence[tuple[float, _BeatEstimate]] | None = None,
    ) -> _BeatDecision:
        """Resolve sparse/dense competition or explicitly reject ambiguity.

        This compares all accepted candidates without assuming a particular
        BPM or an exact 2x relationship.  A denser candidate is credible only
        when it explains materially more individual beats and independently
        meets grid, morphology, spectrum and interval requirements.  Close
        evidence is not guessed: it becomes ``ambiguous_period``.
        """

        ranked = sorted(accepted_estimates, key=lambda item: item[0], reverse=True)
        best_ranking, best = ranked[0]
        comparison_pool = plausible_estimates or ranked

        if self._last_valid_bpm is None and best.bpm < 60.0:
            # At startup, alternating pulse amplitudes can make every second
            # beat dominate autocorrelation and spectral ranking.  Do not let
            # that sparse train establish a half-rate history while a credible
            # near-double-rate train explains substantially more individual
            # peaks.  Waiting is safer than choosing either candidate; later
            # windows normally resolve the morphology as contact settles.
            startup_harmonics = [
                estimate
                for _, estimate in comparison_pool
                if estimate is not best
                and 1.75 <= estimate.bpm / best.bpm <= 2.25
                and estimate.matched_peak_count
                >= best.matched_peak_count + max(
                    3, int(math.ceil(best.matched_peak_count * 0.50))
                )
                and estimate.grid_confidence >= 0.55
                and estimate.morphology_consistency >= 0.45
                and estimate.relative_spectral_power >= 0.10
                and estimate.period_agreement <= 0.12
                and estimate.confidence >= 0.60
            ]
            if startup_harmonics:
                return _BeatDecision(None, "ambiguous_period")

        minimum_extra_beats = max(
            2, int(math.ceil(best.matched_peak_count * 0.35))
        )
        competitors = [
            (ranking, estimate)
            for ranking, estimate in comparison_pool
            if estimate is not best
            if estimate.matched_peak_count
            >= best.matched_peak_count + minimum_extra_beats
            and estimate.grid_confidence >= 0.55
            and estimate.morphology_consistency >= 0.70
            and estimate.relative_spectral_power >= 0.10
            and estimate.period_agreement <= 0.12
            and abs(estimate.bpm - best.bpm) / best.bpm > 0.12
        ]
        if not competitors:
            return _BeatDecision(best, "estimated")
        competitors.sort(key=lambda item: item[0], reverse=True)
        competitor_ranking, competitor = competitors[0]
        if self._last_valid_bpm is not None:
            tracked = [
                estimate
                for estimate in (best, competitor)
                if abs(estimate.bpm - self._last_valid_bpm)
                / self._last_valid_bpm
                <= 0.09
            ]
            if len(tracked) == 1:
                # Once established, a still-credible beat train near the
                # tracked IBI wins over a newly appearing pressure-wave
                # candidate.  This is per-device continuity, not cross-device
                # reference data, and it cannot establish a startup value.
                return _BeatDecision(tracked[0], "estimated")
        periodicity_advantage = best.periodicity - competitor.periodicity
        spectral_advantage = (
            competitor.relative_spectral_power
            - best.relative_spectral_power
        )
        if (
            best.periodicity < 0.15
            and competitor.periodicity < 0.15
            and spectral_advantage <= -0.50
        ):
            # Jittered weak beats can lose autocorrelation while retaining a
            # dominant spectral line and a consistent local-peak grid.  A
            # much weaker dense alternative must not make that case unusable.
            return _BeatDecision(best, "spectral_dominant")
        if (
            best.periodicity >= 0.50
            and periodicity_advantage >= 0.35
            and spectral_advantage <= -0.20
        ):
            return _BeatDecision(best, "estimated")
        if (
            spectral_advantage >= 0.25
            and competitor.grid_confidence >= best.grid_confidence - 0.05
            and competitor.morphology_consistency >= 0.70
        ):
            return _BeatDecision(competitor, "estimated")
        if (
            competitor.grid_confidence >= best.grid_confidence + 0.05
            and spectral_advantage >= -0.05
            and competitor.morphology_consistency >= 0.70
        ):
            return _BeatDecision(competitor, "estimated")
        score_difference = competitor_ranking - best_ranking
        if score_difference > 0.08:
            return _BeatDecision(competitor, "estimated")
        if score_difference < -0.08:
            # A genuine sparse rhythm should repeat strongly at its own period,
            # not merely win by explaining a large pressure wave.  This is a
            # quality relationship and does not encode a BPM band or ratio.
            if (
                best.periodicity >= 0.50
                and best.periodicity - competitor.periodicity >= 0.35
            ):
                return _BeatDecision(best, "estimated")
        return _BeatDecision(None, "ambiguous_period")

    def _lag_bounds(self, value_count: int) -> tuple[int, int]:
        minimum_lag = max(1, int(math.floor(self.sample_rate_hz * 60.0 / self.max_bpm)))
        maximum_lag = min(
            value_count - 2,
            int(math.ceil(self.sample_rate_hz * 60.0 / self.min_bpm)),
        )
        return minimum_lag, maximum_lag

    def _detect_peaks(
        self, values: list[float], minimum_lag: int, maximum_lag: int
    ) -> list[_DetectedPeak]:
        center = float(statistics.median(values))
        median_deviation = float(
            statistics.median(abs(value - center) for value in values)
        )
        robust_sigma = 1.4826 * median_deviation
        prominence_threshold = max(self.min_signal_rms * 0.5, robust_sigma * 0.35)
        radius = max(2, maximum_lag // 2)

        candidates: list[_DetectedPeak] = []
        for index in range(1, len(values) - 1):
            value = values[index]
            if value <= values[index - 1] or value < values[index + 1]:
                continue
            left_floor = min(values[max(0, index - radius):index])
            right_floor = min(values[index + 1:min(len(values), index + radius + 1)])
            prominence = value - max(left_floor, right_floor)
            if prominence >= prominence_threshold:
                candidates.append(_DetectedPeak(index, prominence))

        minimum_separation = max(1, int(round(minimum_lag * 0.35)))
        accepted: list[_DetectedPeak] = []
        for peak in candidates:
            if accepted and peak.index - accepted[-1].index < minimum_separation:
                if peak.prominence > accepted[-1].prominence:
                    accepted[-1] = peak
                continue
            accepted.append(peak)
        return accepted

    def _spectral_profile(
        self, values: list[float], minimum_lag: int, maximum_lag: int
    ) -> dict[int, float]:
        if len(values) <= 1:
            return {}
        windowed = [
            value * (0.5 - 0.5 * math.cos(2.0 * math.pi * index / (len(values) - 1)))
            for index, value in enumerate(values)
        ]
        profile: dict[int, float] = {}
        for lag in range(minimum_lag, maximum_lag + 1):
            angle = 2.0 * math.pi / lag
            step_real = math.cos(angle)
            step_imag = -math.sin(angle)
            oscillator_real = 1.0
            oscillator_imag = 0.0
            real = 0.0
            imaginary = 0.0
            for value in windowed:
                real += value * oscillator_real
                imaginary += value * oscillator_imag
                next_real = oscillator_real * step_real - oscillator_imag * step_imag
                oscillator_imag = oscillator_real * step_imag + oscillator_imag * step_real
                oscillator_real = next_real
            profile[lag] = real * real + imaginary * imaginary
        return profile

    def _uniform_spectral_bins(
        self, values: Sequence[float]
    ) -> list[tuple[float, float]]:
        """Return an oversampled, uniformly spaced heart-band spectrum."""

        if len(values) <= 1:
            return []
        windowed = [
            value * (0.5 - 0.5 * math.cos(2.0 * math.pi * index / (len(values) - 1)))
            for index, value in enumerate(values)
        ]
        duration_seconds = len(values) / self.sample_rate_hz
        frequency_step = 1.0 / max(1.0, 4.0 * duration_seconds)
        minimum_frequency = self.min_bpm / 60.0
        maximum_frequency = self.max_bpm / 60.0
        bin_count = int(
            math.floor((maximum_frequency - minimum_frequency) / frequency_step)
        ) + 1
        bins: list[tuple[float, float]] = []
        for offset in range(bin_count):
            frequency = minimum_frequency + offset * frequency_step
            angle = 2.0 * math.pi * frequency / self.sample_rate_hz
            step_real = math.cos(angle)
            step_imag = -math.sin(angle)
            oscillator_real = 1.0
            oscillator_imag = 0.0
            real = 0.0
            imaginary = 0.0
            for value in windowed:
                real += value * oscillator_real
                imaginary += value * oscillator_imag
                next_real = oscillator_real * step_real - oscillator_imag * step_imag
                oscillator_imag = oscillator_real * step_imag + oscillator_imag * step_real
                oscillator_real = next_real
            bins.append((frequency, real * real + imaginary * imaginary))
        return bins

    @staticmethod
    def _candidate_spectral_evidence(
        bins: Sequence[tuple[float, float]], candidate_frequency: float
    ) -> tuple[float, float, float, float]:
        """Return local spectral quality and power relative to the band peak."""

        if not bins:
            return 0.0, 0.0, candidate_frequency, 0.0
        local_half_width = max(0.08, candidate_frequency * 0.05)
        local = [
            (frequency, power)
            for frequency, power in bins
            if abs(frequency - candidate_frequency) <= local_half_width
        ]
        if not local:
            local = [min(bins, key=lambda item: abs(item[0] - candidate_frequency))]
        peak_frequency, peak_power = max(local, key=lambda item: item[1])
        floor_values = [
            power
            for frequency, power in bins
            if abs(frequency - peak_frequency) > 0.15
        ]
        spectral_floor = (
            float(statistics.median(floor_values)) if floor_values else 0.0
        )
        peak_to_floor = (
            peak_power / spectral_floor if spectral_floor > 0.0 else 0.0
        )
        total_power = sum(power for _, power in bins)
        concentrated_power = sum(
            power
            for frequency, power in bins
            if abs(frequency - peak_frequency) <= 0.125
        )
        concentration = concentrated_power / total_power if total_power > 0.0 else 0.0
        band_peak_power = max(power for _, power in bins)
        relative_power = (
            peak_power / band_peak_power if band_peak_power > 0.0 else 0.0
        )
        return peak_to_floor, concentration, peak_frequency, relative_power

    @staticmethod
    def _profile_peaks(profile: Mapping[int, float], *, limit: int) -> list[int]:
        if not profile:
            return []
        lags = sorted(profile)
        candidates: list[tuple[float, int]] = []
        for offset, lag in enumerate(lags):
            value = profile[lag]
            previous = profile[lags[offset - 1]] if offset > 0 else -math.inf
            following = profile[lags[offset + 1]] if offset + 1 < len(lags) else -math.inf
            if value >= previous and value >= following:
                candidates.append((value, lag))
        candidates.sort(reverse=True)
        return [lag for _, lag in candidates[:limit]]

    def _match_peak_grid(
        self,
        values: Sequence[float],
        peaks: Sequence[_DetectedPeak],
        period: float,
    ) -> _GridMatch | None:
        """Match a candidate period to locally searched pulse peaks.

        The globally detected peaks remain useful as anchors and as an
        extra-pulse penalty.  Once a period is proposed, however, each grid
        slot searches the filtered waveform again with a lower local
        prominence floor.  This lets a weak but correctly timed pulse
        contribute without making weak peaks globally eligible everywhere.
        """

        value_count = len(values)
        tolerance = max(2.0, min(period * 0.18, self.sample_rate_hz * 0.15))
        edge = int(math.ceil(tolerance))
        interior_peaks = [
            peak for peak in peaks if edge <= peak.index < value_count - edge
        ]

        center = float(statistics.median(values))
        median_deviation = float(
            statistics.median(abs(value - center) for value in values)
        )
        robust_sigma = 1.4826 * median_deviation
        local_prominence_floor = max(
            self.min_signal_rms * 0.10,
            robust_sigma * 0.10,
        )
        local_radius = max(2, int(round(period * 0.30)))
        local_peaks: list[_DetectedPeak] = []
        for index in range(1, value_count - 1):
            value = values[index]
            if value <= values[index - 1] or value < values[index + 1]:
                continue
            left_floor = min(values[max(0, index - local_radius):index])
            right_floor = min(
                values[index + 1:min(value_count, index + local_radius + 1)]
            )
            prominence = value - max(left_floor, right_floor)
            if prominence >= local_prominence_floor:
                local_peaks.append(_DetectedPeak(index, prominence))

        local_peaks = [
            peak for peak in local_peaks if edge <= peak.index < value_count - edge
        ]
        if len(local_peaks) < 5:
            return None

        anchor_by_index = {peak.index: peak for peak in local_peaks}
        for peak in interior_peaks:
            anchor_by_index[peak.index] = peak
        anchors = sorted(
            anchor_by_index.values(),
            key=lambda peak: peak.prominence,
            reverse=True,
        )[:20]
        best: _GridMatch | None = None
        for anchor in anchors:
            first_grid = int(math.ceil((edge - anchor.index) / period))
            last_grid = int(math.floor((value_count - edge - 1 - anchor.index) / period))
            expected_count = last_grid - first_grid + 1
            if expected_count < 5:
                continue

            assignments: dict[int, tuple[float, float, _DetectedPeak]] = {}
            for peak in local_peaks:
                grid_index = int(round((peak.index - anchor.index) / period))
                if not first_grid <= grid_index <= last_grid:
                    continue
                expected = anchor.index + grid_index * period
                error = abs(peak.index - expected)
                if error > tolerance:
                    continue
                match_strength = peak.prominence * (
                    1.0 - 0.25 * error / tolerance
                )
                previous = assignments.get(grid_index)
                if previous is None or match_strength > previous[0]:
                    assignments[grid_index] = (match_strength, error, peak)

            matched_peaks = [assignments[index][2] for index in sorted(assignments)]
            matched = [peak.index for peak in matched_peaks]
            if len(matched) < 5:
                continue
            recall = len(matched) / expected_count
            scoring_peaks = interior_peaks if interior_peaks else local_peaks
            total_prominence = sum(peak.prominence for peak in scoring_peaks)
            explained_prominence = 0.0
            for peak in scoring_peaks:
                nearest_grid = int(round((peak.index - anchor.index) / period))
                if not first_grid <= nearest_grid <= last_grid:
                    continue
                expected = anchor.index + nearest_grid * period
                if abs(peak.index - expected) <= tolerance:
                    explained_prominence += peak.prominence
            precision = (
                explained_prominence / total_prominence
                if total_prominence > 0.0
                else 0.0
            )
            if recall + precision <= 0.0:
                continue
            f1 = 2.0 * recall * precision / (recall + precision)

            normalized_errors: list[float] = []
            for left, right in zip(matched, matched[1:]):
                difference = float(right - left)
                beat_count = max(1, int(round(difference / period)))
                normalized_errors.append(abs(difference / beat_count - period) / period)
            median_error = (
                float(statistics.median(normalized_errors))
                if normalized_errors
                else 1.0
            )
            interval_consistency = max(0.0, 1.0 - median_error / 0.20)
            extra_peak_penalty = min(1.0, precision / 0.60)
            grid_confidence = (
                0.75 * f1 + 0.25 * interval_consistency
            ) * extra_peak_penalty
            morphology_consistency = self._pulse_morphology_consistency(
                values, matched, period
            )
            if best is None or grid_confidence > best.confidence:
                best = _GridMatch(
                    confidence=grid_confidence,
                    matched_indices=tuple(matched),
                    morphology_consistency=morphology_consistency,
                )
        return best

    def _pulse_morphology_consistency(
        self,
        values: Sequence[float],
        matched_indices: Sequence[int],
        period: float,
    ) -> float:
        """Compare amplitude-normalized pulse snippets with a median template."""

        half_width = min(period * 0.22, self.sample_rate_hz * 0.150)
        snippet_records: list[tuple[int, list[float]]] = []
        first_center = matched_indices[0] if matched_indices else 0
        for center in matched_indices:
            start = center - half_width
            end = center + half_width
            if start < 0.0 or end >= len(values) - 1:
                continue
            snippet: list[float] = []
            for point in range(21):
                position = start + (end - start) * point / 20.0
                left = int(math.floor(position))
                fraction = position - left
                sample = (
                    float(values[left]) * (1.0 - fraction)
                    + float(values[left + 1]) * fraction
                )
                snippet.append(sample)

            # Remove the straight baseline between the window edges before
            # normalizing.  Absolute amplitude is intentionally discarded so
            # alternating strong/weak physiological pulses remain compatible.
            left_edge = snippet[0]
            right_edge = snippet[-1]
            detrended = [
                sample
                - (left_edge + (right_edge - left_edge) * point / 20.0)
                for point, sample in enumerate(snippet)
            ]
            amplitude = max(detrended) - min(detrended)
            if amplitude <= 1e-9:
                continue
            normalized = [sample / amplitude for sample in detrended]
            normalized_mean = sum(normalized) / len(normalized)
            grid_slot = int(round((center - first_center) / period))
            snippet_records.append(
                (
                    grid_slot,
                    [sample - normalized_mean for sample in normalized],
                )
            )

        if len(snippet_records) < 5:
            return 0.0
        snippets = [snippet for _, snippet in snippet_records]
        template = [
            float(statistics.median(snippet[point] for snippet in snippets))
            for point in range(21)
        ]
        template_mean = sum(template) / len(template)
        template = [sample - template_mean for sample in template]
        template_energy = sum(sample * sample for sample in template)
        if template_energy <= 1e-12:
            return 0.0

        correlations: list[float] = []
        for snippet in snippets:
            snippet_energy = sum(sample * sample for sample in snippet)
            if snippet_energy <= 1e-12:
                continue
            correlation = sum(
                sample * reference
                for sample, reference in zip(snippet, template)
            ) / math.sqrt(snippet_energy * template_energy)
            correlations.append(max(-1.0, min(1.0, correlation)))
        if not correlations:
            return 0.0
        template_consistency = float(statistics.median(correlations))
        parity_templates: list[list[float]] = []
        for parity in (0, 1):
            group = [
                snippet
                for slot, snippet in snippet_records
                if slot % 2 == parity
            ]
            if len(group) < 2:
                continue
            parity_template = [
                float(statistics.median(snippet[point] for snippet in group))
                for point in range(21)
            ]
            parity_mean = sum(parity_template) / len(parity_template)
            parity_templates.append(
                [sample - parity_mean for sample in parity_template]
            )
        parity_consistency = template_consistency
        if len(parity_templates) == 2:
            left, right = parity_templates
            left_energy = sum(sample * sample for sample in left)
            right_energy = sum(sample * sample for sample in right)
            if left_energy > 1e-12 and right_energy > 1e-12:
                parity_consistency = sum(
                    left_sample * right_sample
                    for left_sample, right_sample in zip(left, right)
                ) / math.sqrt(left_energy * right_energy)
        return max(0.0, min(template_consistency, parity_consistency))

    def _best_autocorrelation(self, values: list[float]) -> tuple[int | None, float]:
        minimum_lag, maximum_lag = self._lag_bounds(len(values))
        if maximum_lag < minimum_lag:
            return None, 0.0

        best_lag: int | None = None
        best_correlation = -1.0
        for lag in range(minimum_lag, maximum_lag + 1):
            correlation = self._normalized_correlation(values, lag)
            if correlation > best_correlation:
                best_correlation = correlation
                best_lag = lag
        return best_lag, best_correlation

    @staticmethod
    def _normalized_correlation(values: list[float], lag: int) -> float:
        count = len(values) - lag
        left = values[:count]
        right = values[lag:]
        left_energy = sum(value * value for value in left)
        right_energy = sum(value * value for value in right)
        if left_energy <= 0.0 or right_energy <= 0.0:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / math.sqrt(left_energy * right_energy)

    def _empty_result(self, state: str) -> HeartRateResult:
        return HeartRateResult(
            bpm=None,
            state=state,
            quality=0.0,
            periodicity=0.0,
            signal_rms=0.0,
            sample_missing_rate=0.0,
            max_gap_ms=0,
            window_end_sample_seq=None,
            window_end_timestamp_ms=None,
            observed_samples=0,
            missing_samples=0,
        )

    def _result(
        self,
        *,
        state: str,
        bpm: float | None = None,
        quality: float = 0.0,
        periodicity: float = 0.0,
        signal_rms: float = 0.0,
        missing_rate: float,
        max_gap_ms: int,
        observed_samples: int,
        missing_samples: int,
    ) -> HeartRateResult:
        last = self._samples[-1]
        return HeartRateResult(
            bpm=bpm,
            state=state,
            quality=quality,
            periodicity=periodicity,
            signal_rms=signal_rms,
            sample_missing_rate=missing_rate * 100.0,
            max_gap_ms=max_gap_ms,
            window_end_sample_seq=last.sequence,
            window_end_timestamp_ms=last.timestamp_ms,
            observed_samples=observed_samples,
            missing_samples=missing_samples,
        )
