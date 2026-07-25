import argparse
import binascii
import csv
import json
import socket
import statistics
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import serial
from serial.tools import list_ports

from heart_rate import HeartRateAnalyzer, HeartRateResult
from t5_audio import T5AudioLink


DEFAULT_INTERACTION_DEVICES = ("person_01", "person_40")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTEGRATED_LIVE_OUTPUT = (
    PROJECT_ROOT / "outputs" / "ADX_Flower_PointCloud" / "live" / "sensor_live.csv"
)
INTERACTION_CALIBRATION_WARMUP_SECONDS = 8.0
INTERACTION_HYSTERESIS_DB = 3.0
INTERACTION_CALIBRATION_MIN_SAMPLES = 10


@dataclass(frozen=True)
class InteractionThresholds:
    enter_rssi_dbm: float = -87.0
    exit_rssi_dbm: float = -90.0


@dataclass(frozen=True)
class InteractionZoneConfig:
    devices: dict[str, InteractionThresholds]
    enter_confirmations: int = 3
    exit_confirmations: int = 3
    stale_seconds: float = 2.5

    @classmethod
    def defaults(cls) -> "InteractionZoneConfig":
        return cls(
            {
                device_id: InteractionThresholds()
                for device_id in DEFAULT_INTERACTION_DEVICES
            }
        )


def load_interaction_zone_config(path: Path | None) -> InteractionZoneConfig:
    if path is None:
        return InteractionZoneConfig.defaults()
    with path.open(encoding="utf-8") as handle:
        content = json.load(handle)
    devices = content.get("devices")
    if not isinstance(devices, dict):
        raise ValueError("interaction config must contain a devices object")
    if set(devices) != set(DEFAULT_INTERACTION_DEVICES):
        raise ValueError(
            "interaction config devices must be person_01 and person_40"
        )
    thresholds = {}
    for device_id, values in devices.items():
        try:
            enter = float(values["enter_rssi_dbm"])
            exit_ = float(values["exit_rssi_dbm"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid thresholds for {device_id}") from error
        if not -110 <= exit_ < enter <= -10:
            raise ValueError(
                f"{device_id} requires -110 <= exit < enter <= -10 dBm"
            )
        thresholds[device_id] = InteractionThresholds(enter, exit_)
    try:
        enter_confirmations = int(content.get("enter_confirmations", 3))
        exit_confirmations = int(content.get("exit_confirmations", 3))
        stale_seconds = float(content.get("stale_seconds", 2.5))
    except (TypeError, ValueError) as error:
        raise ValueError("invalid interaction confirmation or stale setting") from error
    if enter_confirmations <= 0 or exit_confirmations <= 0 or stale_seconds <= 0:
        raise ValueError("interaction confirmations and stale_seconds must be positive")
    return InteractionZoneConfig(
        thresholds, enter_confirmations, exit_confirmations, stale_seconds
    )


def robust_boundary_rssi(values: list[float]) -> tuple[float, list[float]]:
    center = float(statistics.median(values))
    mad = float(statistics.median(abs(value - center) for value in values))
    threshold = max(3.0, 3.0 * 1.4826 * mad)
    retained = [value for value in values if abs(value - center) <= threshold]
    if len(retained) < max(10, len(values) // 2):
        raise RuntimeError("boundary RSSI is too unstable after outlier filtering")
    return float(statistics.median(retained)), retained


def calibrate_interaction_zone(
    port: str,
    baudrate: int,
    duration_seconds: float,
    save_path: Path,
) -> InteractionZoneConfig:
    if duration_seconds <= 0:
        raise ValueError("interaction calibration duration must be positive")
    parser = FrameParser()
    samples = {device_id: [] for device_id in DEFAULT_INTERACTION_DEVICES}
    print(
        "Stand still at the interaction boundary. "
        f"Warming up for {INTERACTION_CALIBRATION_WARMUP_SECONDS:g}s...",
        flush=True,
    )
    with serial.Serial(port, baudrate, timeout=0.2) as connection:
        warmup_until = time.monotonic() + INTERACTION_CALIBRATION_WARMUP_SECONDS
        while time.monotonic() < warmup_until:
            parser.feed(connection.read(connection.in_waiting or 1))
        print(f"Capturing boundary RSSI for {duration_seconds:g}s...", flush=True)
        capture_until = time.monotonic() + duration_seconds
        while time.monotonic() < capture_until:
            raw = connection.read(connection.in_waiting or 1)
            for packet in parser.feed(raw):
                if packet.get("type") != "sensor_context_packet":
                    continue
                device_id = packet.get("device_id")
                rssi = packet.get("ranging_rssi_filtered_dbm")
                if device_id in samples and rssi is not None:
                    samples[device_id].append(float(rssi))

    minimum_samples = INTERACTION_CALIBRATION_MIN_SAMPLES
    missing = {
        device_id: len(values)
        for device_id, values in samples.items()
        if len(values) < minimum_samples
    }
    if missing:
        raise RuntimeError(
            "insufficient boundary RSSI: "
            + ", ".join(
                f"{device_id} received {count}"
                for device_id, count in missing.items()
            )
            + f"; need at least {minimum_samples} valid samples per direction"
        )

    half_hysteresis = INTERACTION_HYSTERESIS_DB / 2.0
    thresholds = {}
    report_devices = {}
    for device_id, values in samples.items():
        boundary, retained = robust_boundary_rssi(values)
        enter = round((boundary + half_hysteresis) * 2.0) / 2.0
        exit_ = round((boundary - half_hysteresis) * 2.0) / 2.0
        thresholds[device_id] = InteractionThresholds(enter, exit_)
        report_devices[device_id] = {
            "boundary_rssi_dbm": boundary,
            "enter_rssi_dbm": enter,
            "exit_rssi_dbm": exit_,
            "original_sample_count": len(values),
            "retained_sample_count": len(retained),
            "rejected_sample_count": len(values) - len(retained),
            "standard_deviation_db": round(statistics.stdev(retained), 3),
            "minimum_dbm": min(retained),
            "maximum_dbm": max(retained),
        }

    config = InteractionZoneConfig(thresholds)
    rendered = {
        "calibrated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "calibration_method": "standing_at_interaction_boundary",
        "hysteresis_db": INTERACTION_HYSTERESIS_DB,
        "devices": {
            device_id: {
                "enter_rssi_dbm": values.enter_rssi_dbm,
                "exit_rssi_dbm": values.exit_rssi_dbm,
            }
            for device_id, values in config.devices.items()
        },
        "enter_confirmations": config.enter_confirmations,
        "exit_confirmations": config.exit_confirmations,
        "stale_seconds": config.stale_seconds,
        "calibration_statistics": report_devices,
    }
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(
        json.dumps(rendered, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved interaction boundary calibration to {save_path}", flush=True)
    for device_id, values in report_devices.items():
        print(
            f"{device_id}: boundary={values['boundary_rssi_dbm']:g} dBm, "
            f"enter={values['enter_rssi_dbm']:g}, "
            f"exit={values['exit_rssi_dbm']:g}, "
            f"n={values['retained_sample_count']}/"
            f"{values['original_sample_count']}, "
            f"sd={values['standard_deviation_db']:.2f} dB",
            flush=True,
        )
    return config


PPG_VALUE_FIELDS = [
    field
    for index in range(10)
    for field in (f"ppg_red_{index}", f"ppg_ir_{index}")
]

IMU_RAW_FIELDS = [
    f"imu_{axis}_raw_{index}"
    for index in range(5)
    for axis in ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z")
]

IMU_SCALED_FIELDS = [
    f"imu_{axis}_{unit}_{index}"
    for index in range(5)
    for axis, unit in (
        ("accel_x", "g"),
        ("accel_y", "g"),
        ("accel_z", "g"),
        ("gyro_x", "dps"),
        ("gyro_y", "dps"),
        ("gyro_z", "dps"),
    )
]

WIDE_CSV_FIELDS = [
    "row_received_at",
    "device_id",
    "source_mac",
    "pair_delta_ms",
    "ppg_present",
    "ppg_received_at",
    "ppg_packet_seq",
    "ppg_first_sample_seq",
    "ppg_first_sample_timestamp_ms",
    "ppg_sample_rate_hz",
    "ppg_sample_count",
    *PPG_VALUE_FIELDS,
    "context_present",
    "context_received_at",
    "context_packet_seq",
    "context_first_sample_seq",
    "context_first_sample_timestamp_ms",
    "imu_sample_rate_hz",
    "imu_sample_count",
    "imu_valid",
    *IMU_RAW_FIELDS,
    *IMU_SCALED_FIELDS,
    "distance_mm",
    "distance_age_ms",
    "distance_valid",
    "distance_extrapolated",
    "distance_source",
    "ble_rssi_dbm",
    "ranging_peer_device_id",
    "ranging_rssi_raw_dbm",
    "ranging_rssi_filtered_dbm",
    "ranging_tx_power_qdbm",
    "ranging_config_version",
    "clock_synced",
    "context_flags",
    "zone_state",
    "zone_name",
    "within_one_meter",
    "range_trend_state",
    "range_trend_name",
    "range_trend_delta_db",
    "heart_rate_updated",
    "heart_rate_window_end_sample_seq",
    "heart_rate_window_end_timestamp_ms",
    "bpm",
    "heart_rate_state",
    "heart_rate_quality",
    "heart_rate_periodicity",
    "heart_rate_signal_rms",
    "ppg_packet_loss_rate",
    "sample_missing_rate",
    "max_gap_ms",
    "context_gap_rate",
    "ppg_gateway_timestamp_ms",
    "ppg_gateway_received_packets",
    "ppg_invalid_packets",
    "ppg_queue_overflows",
    "context_gateway_timestamp_ms",
    "context_gateway_received_packets",
    "context_invalid_packets",
    "context_queue_overflows",
]

FRAME_SYNC = b"XVP2"
FRAME_VERSION = 1
FRAME_RAW_PPG = 1
FRAME_SENSOR_CONTEXT = 2

TELEMETRY_MAGIC = 0x41565832
TELEMETRY_VERSION = 2
CONTEXT_MAGIC = 0x41565843
CONTEXT_VERSION = 3
GATEWAY_SERIAL_NUMBER = "D4:05:92:7B:86:04"
T5_USB_VID = 0x1A86
T5_USB_PID = 0x55D2


@dataclass(frozen=True)
class HeartRateDisplayResult:
    bpm: float | None
    state: str
    held: bool


class HeartRateDisplayTracker:
    """Keep a clearly marked UI-only BPM for a short signal dropout."""

    def __init__(self, hold_seconds: float = 5.0) -> None:
        if hold_seconds < 0.0:
            raise ValueError("hold_seconds must be non-negative")
        self.hold_seconds = hold_seconds
        self._last_trusted_bpm: float | None = None
        self._last_trusted_at: float | None = None

    def update(
        self, result: HeartRateResult, *, now: float | None = None
    ) -> HeartRateDisplayResult:
        current_time = time.monotonic() if now is None else now
        if result.bpm is not None:
            self._last_trusted_bpm = result.bpm
            self._last_trusted_at = current_time
            return HeartRateDisplayResult(result.bpm, result.state, False)
        if (
            self._last_trusted_bpm is not None
            and self._last_trusted_at is not None
            and 0.0 <= current_time - self._last_trusted_at <= self.hold_seconds
        ):
            return HeartRateDisplayResult(
                self._last_trusted_bpm, "signal_weak/held", True
            )
        return HeartRateDisplayResult(None, result.state, False)


class InteractionZoneTracker:
    """Classify both RSSI directions and emit aggregate interaction edges."""

    def __init__(self, config: InteractionZoneConfig | None = None) -> None:
        self.config = config or InteractionZoneConfig.defaults()
        self.expected_devices = set(self.config.devices)
        self.zone_by_device: dict[str, bool] = {}
        self.last_context_at: dict[str, float] = {}
        self.enter_counts: dict[str, int] = {}
        self.exit_counts: dict[str, int] = {}
        self.stale_rearm_outside: set[str] = set()
        self.stale_rearm_blocked = False
        self.aggregate_inside = False

    def observe(self, packet: dict, *, now: float | None = None) -> str | None:
        if packet.get("type") != "sensor_context_packet":
            return self.poll(now=now)
        current_time = time.monotonic() if now is None else now
        device_id = packet["device_id"]
        if device_id not in self.expected_devices:
            return None
        rssi = packet.get("ranging_rssi_filtered_dbm")
        if rssi is not None:
            self.last_context_at[device_id] = current_time
            thresholds = self.config.devices[device_id]
            if float(rssi) <= thresholds.exit_rssi_dbm:
                self.stale_rearm_outside.add(device_id)
                if self.stale_rearm_outside == self.expected_devices:
                    self.stale_rearm_outside.clear()
                    self.stale_rearm_blocked = False
            elif self.stale_rearm_blocked:
                return self.poll(now=current_time)
            inside = self.zone_by_device.get(device_id, False)
            if not inside:
                self.exit_counts[device_id] = 0
                count = self.enter_counts.get(device_id, 0)
                count = count + 1 if float(rssi) >= thresholds.enter_rssi_dbm else 0
                self.enter_counts[device_id] = count
                if count >= self.config.enter_confirmations:
                    self.zone_by_device[device_id] = True
                    self.enter_counts[device_id] = 0
            else:
                self.enter_counts[device_id] = 0
                count = self.exit_counts.get(device_id, 0)
                count = count + 1 if float(rssi) <= thresholds.exit_rssi_dbm else 0
                self.exit_counts[device_id] = count
                if count >= self.config.exit_confirmations:
                    self.zone_by_device[device_id] = False
                    self.exit_counts[device_id] = 0
        return self.poll(now=current_time)

    def poll(self, *, now: float | None = None) -> str | None:
        current_time = time.monotonic() if now is None else now
        all_fresh = all(
            current_time - self.last_context_at.get(device_id, float("-inf"))
            <= self.config.stale_seconds
            for device_id in self.expected_devices
        )
        if self.aggregate_inside and not all_fresh:
            # A stale direction is a safety stop. Clear both latched states so
            # delayed packets cannot immediately reopen the interaction zone.
            self.zone_by_device.clear()
            self.enter_counts.clear()
            self.exit_counts.clear()
            # Do not split one physical interaction into repeated recordings
            # when a ranging direction is intermittent. Both directions must
            # observe a real exit before a later entry can re-arm recording.
            self.stale_rearm_outside.clear()
            self.stale_rearm_blocked = True
            self.aggregate_inside = False
            return "stop"
        if self.aggregate_inside:
            aggregate_inside = any(
                self.zone_by_device.get(device_id, False)
                for device_id in self.expected_devices
            )
        else:
            aggregate_inside = not self.stale_rearm_blocked and all_fresh and all(
                self.zone_by_device.get(device_id, False)
                for device_id in self.expected_devices
            )
        if aggregate_inside == self.aggregate_inside:
            return None
        self.aggregate_inside = aggregate_inside
        return "start" if aggregate_inside else "stop"

    def device_inside(self, device_id: str) -> bool:
        return self.zone_by_device.get(device_id, False)

SAMPLES_PER_PPG_PACKET = 10
SAMPLES_PER_CONTEXT_PACKET = 5
SENSOR_FLAG_DISTANCE_VALID = 1 << 0
SENSOR_FLAG_IMU_VALID = 1 << 1
SENSOR_FLAG_CLOCK_SYNCED = 1 << 2
SENSOR_FLAG_DISTANCE_ESPNOW_RSSI = 1 << 3
SENSOR_FLAG_DISTANCE_EXTRAPOLATED = 1 << 4

RANGE_TREND_NAMES = {
    0: "unavailable",
    1: "stable",
    2: "approaching",
    3: "receding",
}

PPG_PAYLOAD_STRUCT = struct.Struct("<BBIBBHIIIB3x20II6sIII")
CONTEXT_PACKET_STRUCT = struct.Struct("<IBBHIIIBBBBBbbBHHHh30h")
SERIAL_TRAILER_STRUCT = struct.Struct("<I6sIII")
CONTEXT_PAYLOAD_SIZE = 2 + CONTEXT_PACKET_STRUCT.size + SERIAL_TRAILER_STRUCT.size
MAX_PAYLOAD_SIZE = 1024


@dataclass
class DeviceStats:
    first_sequence: int
    last_sequence: int
    received: int = 1

    def update(self, sequence: int) -> bool:
        if sequence < self.last_sequence:
            if sequence <= 5 and self.last_sequence > 20:
                self.first_sequence = sequence
                self.last_sequence = sequence
                self.received = 1
                return True
            return False
        if sequence == self.last_sequence:
            return False
        self.last_sequence = sequence
        self.received += 1
        return False

    @property
    def expected(self) -> int:
        return self.last_sequence - self.first_sequence + 1

    @property
    def lost(self) -> int:
        return max(0, self.expected - self.received)

    @property
    def loss_rate(self) -> float:
        if self.expected <= 0:
            return 0.0
        return self.lost / self.expected * 100.0


def available_ports() -> list[str]:
    return [port.device for port in list_ports.comports()]


def detect_esp32_port() -> str:
    ports = list(list_ports.comports())
    gateway = [
        port.device
        for port in ports
        if (port.serial_number or "").upper() == GATEWAY_SERIAL_NUMBER
    ]
    if len(gateway) == 1:
        return gateway[0]
    candidates = [port.device for port in ports if port.vid == 0x303A]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError("No Espressif serial port found")
    raise RuntimeError(
        "Multiple Espressif ports found: "
        + ", ".join(candidates)
        + ". Pass the gateway port with --port."
    )


def detect_t5_audio_port() -> str:
    candidates = [
        port.device
        for port in list_ports.comports()
        if port.vid == T5_USB_VID
        and port.pid == T5_USB_PID
        and "SERIAL-B" in (port.description or "").upper()
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError("No T5AI-Core CH342 SERIAL-B audio port found")
    raise RuntimeError(
        "Multiple T5AI-Core audio ports found: "
        + ", ".join(candidates)
        + ". Pass the T5 audio port with --t5-port."
    )


def start_optional_t5_audio(
    port: str | None, output_dir: Path, baudrate: int
) -> T5AudioLink | None:
    """Start the independent audio branch without blocking sensor capture."""
    if port is None:
        return None
    candidate = T5AudioLink(port, output_dir, baudrate)
    try:
        candidate.start()
    except (OSError, RuntimeError, serial.SerialException) as error:
        candidate.close()
        print(
            f"Warning: T5 audio could not start on {port} ({error}); "
            "continuing the sensor CSV chain.",
            file=sys.stderr,
            flush=True,
        )
        return None
    return candidate


def crc16_ccitt(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


def format_mac(raw: bytes) -> str:
    return ":".join(f"{byte:02X}" for byte in raw)


def parse_ppg_payload(payload: bytes) -> dict | None:
    if len(payload) != PPG_PAYLOAD_STRUCT.size:
        return None

    values = PPG_PAYLOAD_STRUCT.unpack(payload)
    (
        frame_version,
        frame_type,
        magic,
        telemetry_version,
        device_id,
        sample_rate_hz,
        packet_sequence,
        first_sample_sequence,
        first_sample_timestamp_ms,
        sample_count,
        *tail,
    ) = values
    if (
        frame_version != FRAME_VERSION
        or frame_type != FRAME_RAW_PPG
        or magic != TELEMETRY_MAGIC
        or telemetry_version != TELEMETRY_VERSION
        or not 0 < sample_count <= SAMPLES_PER_PPG_PACKET
        or sample_rate_hz <= 0
    ):
        return None

    sample_values = tail[: SAMPLES_PER_PPG_PACKET * 2]
    gateway_timestamp_ms = tail[SAMPLES_PER_PPG_PACKET * 2]
    source_mac = tail[SAMPLES_PER_PPG_PACKET * 2 + 1]
    received_packets, invalid_packets, queue_overflows = tail[-3:]
    samples = [
        [sample_values[index], sample_values[index + 1]]
        for index in range(0, sample_count * 2, 2)
    ]
    return {
        "type": "raw_ppg_packet",
        "device_id": f"person_{device_id:02d}",
        "packet_seq": packet_sequence,
        "first_sample_seq": first_sample_sequence,
        "first_sample_timestamp_ms": first_sample_timestamp_ms,
        "sample_rate_hz": sample_rate_hz,
        "sample_count": sample_count,
        "gateway_timestamp_ms": gateway_timestamp_ms,
        "source_mac": format_mac(source_mac),
        "samples": samples,
        "received_packets": received_packets,
        "invalid_packets": invalid_packets,
        "queue_overflows": queue_overflows,
    }


def parse_context_payload(payload: bytes) -> dict | None:
    if len(payload) != CONTEXT_PAYLOAD_SIZE:
        return None
    frame_version, frame_type = struct.unpack_from("<BB", payload)
    if frame_version != FRAME_VERSION or frame_type != FRAME_SENSOR_CONTEXT:
        return None

    packet_offset = 2
    values = CONTEXT_PACKET_STRUCT.unpack_from(payload, packet_offset)
    (
        magic,
        context_version,
        device_id,
        imu_sample_rate_hz,
        packet_sequence,
        first_sample_sequence,
        first_sample_timestamp_ms,
        sample_count,
        flags,
        zone_state,
        range_trend_state,
        ranging_peer_device_id,
        ranging_rssi_raw_dbm,
        ranging_rssi_filtered_dbm,
        ranging_tx_power_qdbm,
        ranging_config_version,
        distance_mm,
        distance_age_ms,
        range_trend_delta_centi_db,
        *imu_values,
    ) = values
    if (
        magic != CONTEXT_MAGIC
        or context_version != CONTEXT_VERSION
        or imu_sample_rate_hz <= 0
        or not 0 < sample_count <= SAMPLES_PER_CONTEXT_PACKET
        or zone_state not in (0, 1)
        or range_trend_state not in RANGE_TREND_NAMES
    ):
        return None

    trailer_offset = packet_offset + CONTEXT_PACKET_STRUCT.size
    (
        gateway_timestamp_ms,
        source_mac,
        received_packets,
        invalid_packets,
        queue_overflows,
    ) = SERIAL_TRAILER_STRUCT.unpack_from(payload, trailer_offset)
    samples = [
        imu_values[index : index + 6]
        for index in range(0, sample_count * 6, 6)
    ]
    return {
        "type": "sensor_context_packet",
        "device_id": f"person_{device_id:02d}",
        "packet_seq": packet_sequence,
        "first_sample_seq": first_sample_sequence,
        "first_sample_timestamp_ms": first_sample_timestamp_ms,
        "sample_rate_hz": imu_sample_rate_hz,
        "imu_sample_rate_hz": imu_sample_rate_hz,
        "sample_count": sample_count,
        "flags": flags,
        "distance_valid": bool(flags & SENSOR_FLAG_DISTANCE_VALID),
        "distance_extrapolated": bool(
            flags & SENSOR_FLAG_DISTANCE_EXTRAPOLATED
        ),
        "distance_source": (
            "espnow_rssi"
            if flags & SENSOR_FLAG_DISTANCE_ESPNOW_RSSI
            else "ultrasonic"
        ),
        "ble_rssi_dbm": None,
        "ranging_peer_device_id": (
            f"person_{ranging_peer_device_id:02d}"
            if ranging_peer_device_id
            else None
        ),
        "ranging_rssi_raw_dbm": (
            ranging_rssi_raw_dbm if ranging_rssi_raw_dbm else None
        ),
        "ranging_rssi_filtered_dbm": (
            ranging_rssi_filtered_dbm if ranging_rssi_filtered_dbm else None
        ),
        "ranging_tx_power_qdbm": ranging_tx_power_qdbm,
        "ranging_config_version": ranging_config_version,
        "imu_valid": bool(flags & SENSOR_FLAG_IMU_VALID),
        "clock_synced": bool(flags & SENSOR_FLAG_CLOCK_SYNCED),
        "zone_state": zone_state,
        "zone_name": "inside" if zone_state else "outside",
        "within_one_meter": bool(zone_state),
        "range_trend_state": range_trend_state,
        "range_trend_name": RANGE_TREND_NAMES[range_trend_state],
        "range_trend_delta_db": (
            range_trend_delta_centi_db / 100.0
            if range_trend_state != 0
            else None
        ),
        "distance_mm": distance_mm,
        "distance_age_ms": distance_age_ms,
        "gateway_timestamp_ms": gateway_timestamp_ms,
        "source_mac": format_mac(source_mac),
        "samples": samples,
        "received_packets": received_packets,
        "invalid_packets": invalid_packets,
        "queue_overflows": queue_overflows,
    }


def parse_payload(payload: bytes) -> dict | None:
    if len(payload) < 2:
        return None
    frame_version, frame_type = struct.unpack_from("<BB", payload)
    if frame_version != FRAME_VERSION:
        return None
    if frame_type == FRAME_RAW_PPG:
        return parse_ppg_payload(payload)
    if frame_type == FRAME_SENSOR_CONTEXT:
        return parse_context_payload(payload)
    return None


class FrameParser:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.crc_errors = 0

    def feed(self, data: bytes) -> list[dict]:
        self.buffer.extend(data)
        packets = []
        while True:
            sync_index = self.buffer.find(FRAME_SYNC)
            if sync_index < 0:
                if len(self.buffer) > len(FRAME_SYNC) - 1:
                    del self.buffer[: -(len(FRAME_SYNC) - 1)]
                break
            if sync_index:
                del self.buffer[:sync_index]
            if len(self.buffer) < 6:
                break

            payload_length = struct.unpack_from("<H", self.buffer, 4)[0]
            if payload_length > MAX_PAYLOAD_SIZE:
                del self.buffer[0]
                continue
            frame_length = 6 + payload_length + 2
            if len(self.buffer) < frame_length:
                break

            payload = bytes(self.buffer[6 : 6 + payload_length])
            expected_crc = struct.unpack_from("<H", self.buffer, 6 + payload_length)[0]
            if crc16_ccitt(payload) != expected_crc:
                self.crc_errors += 1
                del self.buffer[0]
                continue

            del self.buffer[:frame_length]
            packet = parse_payload(payload)
            if packet is not None:
                packets.append(packet)
        return packets


def open_gateway_serial(port: str, baudrate: int) -> serial.Serial:
    """Open the S3 HWCDC port without asserting its reset control line.

    On the current Windows host, pyserial's default RTS=True state can leave
    this ESP32-S3 native USB CDC port blocked during open.  Configure the line
    states before opening: DTR advertises an attached host, while RTS remains
    released so the gateway keeps running.
    """
    connection = serial.Serial(port=None, baudrate=baudrate, timeout=1)
    connection.port = port
    connection.dtr = True
    connection.rts = False
    connection.open()
    return connection


@dataclass(frozen=True)
class BufferedPacket:
    packet: dict
    received_at: str
    arrived_at: float
    loss_rate: float
    heart_rate: HeartRateResult | None = None
    heart_rate_updated: bool = False


class WideRowMerger:
    """Pair PPG/context packets without resampling either sensor stream."""

    def __init__(self, pair_tolerance_ms: int = 60, flush_after_seconds: float = 0.25):
        self.pair_tolerance_ms = pair_tolerance_ms
        self.flush_after_seconds = flush_after_seconds
        self._ppg: dict[str, list[BufferedPacket]] = {}
        self._context: dict[str, list[BufferedPacket]] = {}

    def add(self, entry: BufferedPacket) -> list[tuple[BufferedPacket | None, BufferedPacket | None]]:
        device_id = str(entry.packet["device_id"])
        is_ppg = entry.packet["type"] == "raw_ppg_packet"
        own = self._ppg if is_ppg else self._context
        opposite = self._context if is_ppg else self._ppg
        candidates = opposite.get(device_id, [])
        entry_timestamp = int(entry.packet["first_sample_timestamp_ms"])
        best: tuple[int, int] | None = None
        for index, candidate in enumerate(candidates):
            delta = abs(
                int(candidate.packet["first_sample_timestamp_ms"]) - entry_timestamp
            )
            if delta <= self.pair_tolerance_ms and (best is None or delta < best[0]):
                best = (delta, index)

        rows: list[tuple[BufferedPacket | None, BufferedPacket | None]] = []
        if best is not None:
            counterpart = candidates.pop(best[1])
            if is_ppg:
                rows.append((entry, counterpart))
            else:
                rows.append((counterpart, entry))
        else:
            own.setdefault(device_id, []).append(entry)
        rows.extend(self.flush_stale(entry.arrived_at))
        return rows

    def flush_stale(
        self, now: float
    ) -> list[tuple[BufferedPacket | None, BufferedPacket | None]]:
        cutoff = now - self.flush_after_seconds
        rows: list[tuple[BufferedPacket | None, BufferedPacket | None]] = []
        for pending, ppg_side in ((self._ppg, True), (self._context, False)):
            for device_id in list(pending):
                keep: list[BufferedPacket] = []
                for entry in pending[device_id]:
                    if entry.arrived_at <= cutoff:
                        rows.append((entry, None) if ppg_side else (None, entry))
                    else:
                        keep.append(entry)
                if keep:
                    pending[device_id] = keep
                else:
                    del pending[device_id]
        return rows

    def flush_all(self) -> list[tuple[BufferedPacket | None, BufferedPacket | None]]:
        rows: list[tuple[BufferedPacket | None, BufferedPacket | None]] = []
        for entries in self._ppg.values():
            rows.extend((entry, None) for entry in entries)
        for entries in self._context.values():
            rows.extend((None, entry) for entry in entries)
        self._ppg.clear()
        self._context.clear()
        return rows


def wide_row(
    ppg_entry: BufferedPacket | None,
    context_entry: BufferedPacket | None,
) -> dict:
    if ppg_entry is None and context_entry is None:
        raise ValueError("a wide row needs at least one source packet")
    ppg = ppg_entry.packet if ppg_entry is not None else None
    context = context_entry.packet if context_entry is not None else None
    source = ppg or context
    assert source is not None
    row: dict = {
        "row_received_at": (
            ppg_entry.received_at if ppg_entry is not None else context_entry.received_at
        ),
        "device_id": source["device_id"],
        "source_mac": source.get("source_mac", ""),
        "ppg_present": int(ppg is not None),
        "context_present": int(context is not None),
    }

    if ppg is not None and ppg_entry is not None:
        row.update(
            {
                "ppg_received_at": ppg_entry.received_at,
                "ppg_packet_seq": int(ppg["packet_seq"]),
                "ppg_first_sample_seq": int(ppg["first_sample_seq"]),
                "ppg_first_sample_timestamp_ms": int(ppg["first_sample_timestamp_ms"]),
                "ppg_sample_rate_hz": int(ppg["sample_rate_hz"]),
                "ppg_sample_count": int(ppg["sample_count"]),
                "ppg_packet_loss_rate": f"{ppg_entry.loss_rate:.4f}",
                "ppg_gateway_timestamp_ms": int(ppg.get("gateway_timestamp_ms", 0)),
                "ppg_gateway_received_packets": int(ppg.get("received_packets", 0)),
                "ppg_invalid_packets": int(ppg.get("invalid_packets", 0)),
                "ppg_queue_overflows": int(ppg.get("queue_overflows", 0)),
            }
        )
        for index, sample in enumerate(ppg["samples"]):
            row[f"ppg_red_{index}"] = int(sample[0])
            row[f"ppg_ir_{index}"] = int(sample[1])

        result = ppg_entry.heart_rate
        if result is not None:
            row.update(
                {
                    "heart_rate_updated": int(ppg_entry.heart_rate_updated),
                    "heart_rate_window_end_sample_seq": (
                        result.window_end_sample_seq
                        if result.window_end_sample_seq is not None
                        else ""
                    ),
                    "heart_rate_window_end_timestamp_ms": (
                        result.window_end_timestamp_ms
                        if result.window_end_timestamp_ms is not None
                        else ""
                    ),
                    "bpm": f"{result.bpm:.2f}" if result.bpm is not None else "",
                    "heart_rate_state": result.state,
                    "heart_rate_quality": f"{result.quality:.4f}",
                    "heart_rate_periodicity": f"{result.periodicity:.4f}",
                    "heart_rate_signal_rms": f"{result.signal_rms:.4f}",
                    "sample_missing_rate": f"{result.sample_missing_rate:.4f}",
                    "max_gap_ms": result.max_gap_ms,
                }
            )

    if context is not None and context_entry is not None:
        row.update(
            {
                "context_received_at": context_entry.received_at,
                "context_packet_seq": int(context["packet_seq"]),
                "context_first_sample_seq": int(context["first_sample_seq"]),
                "context_first_sample_timestamp_ms": int(
                    context["first_sample_timestamp_ms"]
                ),
                "imu_sample_rate_hz": int(context["imu_sample_rate_hz"]),
                "imu_sample_count": int(context["sample_count"]),
                "imu_valid": int(bool(context["imu_valid"])),
                "distance_mm": (
                    int(context["distance_mm"]) if context["distance_valid"] else ""
                ),
                "distance_age_ms": int(context["distance_age_ms"]),
                "distance_valid": int(bool(context["distance_valid"])),
                "distance_extrapolated": int(
                    bool(context["distance_extrapolated"])
                ),
                "distance_source": context["distance_source"],
                "ble_rssi_dbm": (
                    int(context["ble_rssi_dbm"])
                    if context["ble_rssi_dbm"] is not None
                    else ""
                ),
                "ranging_peer_device_id": (
                    context["ranging_peer_device_id"]
                    if context["ranging_peer_device_id"] is not None
                    else ""
                ),
                "ranging_rssi_raw_dbm": (
                    int(context["ranging_rssi_raw_dbm"])
                    if context["ranging_rssi_raw_dbm"] is not None
                    else ""
                ),
                "ranging_rssi_filtered_dbm": (
                    int(context["ranging_rssi_filtered_dbm"])
                    if context["ranging_rssi_filtered_dbm"] is not None
                    else ""
                ),
                "ranging_tx_power_qdbm": int(
                    context["ranging_tx_power_qdbm"]
                ),
                "ranging_config_version": int(
                    context["ranging_config_version"]
                ),
                "clock_synced": int(bool(context["clock_synced"])),
                "context_flags": int(context["flags"]),
                "zone_state": int(context["zone_state"]),
                "zone_name": context["zone_name"],
                "within_one_meter": int(bool(context["within_one_meter"])),
                "range_trend_state": int(context["range_trend_state"]),
                "range_trend_name": context["range_trend_name"],
                "range_trend_delta_db": (
                    f"{context['range_trend_delta_db']:.2f}"
                    if context["range_trend_delta_db"] is not None
                    else ""
                ),
                "context_gap_rate": f"{context_entry.loss_rate:.4f}",
                "context_gateway_timestamp_ms": int(
                    context.get("gateway_timestamp_ms", 0)
                ),
                "context_gateway_received_packets": int(
                    context.get("received_packets", 0)
                ),
                "context_invalid_packets": int(context.get("invalid_packets", 0)),
                "context_queue_overflows": int(context.get("queue_overflows", 0)),
            }
        )
        axes = ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z")
        for index, sample in enumerate(context["samples"]):
            for axis, raw_value in zip(axes, sample):
                row[f"imu_{axis}_raw_{index}"] = int(raw_value)
            for axis, raw_value in zip(axes[:3], sample[:3]):
                row[f"imu_{axis}_g_{index}"] = int(raw_value) / 16384.0
            for axis, raw_value in zip(axes[3:], sample[3:]):
                row[f"imu_{axis}_dps_{index}"] = int(raw_value) / 131.0

    if ppg is not None and context is not None:
        row["pair_delta_ms"] = (
            int(context["first_sample_timestamp_ms"])
            - int(ppg["first_sample_timestamp_ms"])
        )
    return row


def parse_udp_target(value: str) -> tuple[str, int]:
    host, separator, port = value.rpartition(":")
    if not separator or not host:
        raise argparse.ArgumentTypeError("use HOST:PORT, for example 127.0.0.1:8765")
    try:
        port_number = int(port)
    except ValueError as error:
        raise argparse.ArgumentTypeError("UDP port must be an integer") from error
    if not 1 <= port_number <= 65535:
        raise argparse.ArgumentTypeError("UDP port must be between 1 and 65535")
    return host, port_number


def update_stats(
    stats_by_stream: dict[tuple[str, str], DeviceStats], packet: dict
) -> tuple[DeviceStats, bool]:
    key = (str(packet["type"]), str(packet["device_id"]))
    sequence = int(packet["packet_seq"])
    stats = stats_by_stream.get(key)
    if stats is None:
        stats = DeviceStats(sequence, sequence)
        stats_by_stream[key] = stats
        return stats, False
    return stats, stats.update(sequence)


def print_packet_status(
    packet: dict,
    stats: DeviceStats,
    heart_rate: HeartRateResult | None = None,
    display_heart_rate: HeartRateDisplayResult | None = None,
    host_node_inside: bool | None = None,
    interaction_inside: bool | None = None,
) -> None:
    if packet["type"] == "raw_ppg_packet":
        if heart_rate is not None:
            display = display_heart_rate or HeartRateDisplayResult(
                heart_rate.bpm, heart_rate.state, False
            )
            bpm = f"{display.bpm:.1f}" if display.bpm is not None else "--"
            print(
                f"{packet['device_id']} bpm {bpm}  state {display.state}  "
                f"quality {heart_rate.quality:.2f}  "
                f"sample_missing {heart_rate.sample_missing_rate:.1f}%  "
                f"max_gap {heart_rate.max_gap_ms} ms  "
                f"ppg_packet_loss {stats.loss_rate:.1f}%  "
                f"gateway queue overflows {int(packet.get('queue_overflows', 0))}",
                flush=True,
            )
            return
        print(
            f"{packet['device_id']} PPG packet {int(packet['packet_seq']):7d}  "
            f"ppg_packet_loss {stats.lost:5d} ({stats.loss_rate:5.2f}%)  "
            f"gateway queue overflows {int(packet.get('queue_overflows', 0)):5d}",
            flush=True,
        )
        return

    rssi = (
        str(packet["ranging_rssi_filtered_dbm"])
        if packet["ranging_rssi_filtered_dbm"] is not None
        else "--"
    )
    interaction = (
        "inside"
        if interaction_inside
        else "outside"
        if interaction_inside is not None
        else "--"
    )
    host_node_zone = (
        "inside"
        if host_node_inside
        else "outside"
        if host_node_inside is not None
        else "--"
    )
    print(
        f"{packet['device_id']} context packet {int(packet['packet_seq']):7d}  "
        f"rssi {rssi:>4}  "
        f"host_node_zone {host_node_zone:<7}  "
        f"interaction_zone {interaction:<7}  "
        f"trend {packet['range_trend_name']:<11}  "
        f"context_gap {stats.lost:5d} ({stats.loss_rate:5.2f}%)",
        flush=True,
    )


def collect(
    port: str,
    baudrate: int,
    output: Path,
    udp_target: tuple[str, int] | None = None,
    *,
    live_bpm: bool = False,
    duration_seconds: float | None = None,
    t5_audio: T5AudioLink | None = None,
    interaction_config: InteractionZoneConfig | None = None,
) -> None:
    if duration_seconds is not None and duration_seconds <= 0:
        raise ValueError("duration_seconds must be greater than zero")
    output.parent.mkdir(parents=True, exist_ok=True)
    stats_by_stream: dict[tuple[str, str], DeviceStats] = {}
    analyzers: dict[str, HeartRateAnalyzer] = {}
    display_trackers: dict[str, HeartRateDisplayTracker] = {}
    last_status_by_stream: dict[tuple[str, str], float] = {}

    print(f"Opening gateway {port} at {baudrate} baud")
    print(f"Recording all sensor data to {output}")
    print("Press Ctrl+C to stop safely")

    last_data_time = time.monotonic()
    stop_at = (
        last_data_time + duration_seconds
        if duration_seconds is not None
        else None
    )
    last_waiting_message = last_data_time
    last_flush_time = last_data_time
    frame_parser = FrameParser()
    row_merger = WideRowMerger()
    zone_tracker = InteractionZoneTracker(interaction_config)
    config = zone_tracker.config
    threshold_summary = ", ".join(
        f"{device_id} enter={values.enter_rssi_dbm:g}/exit={values.exit_rssi_dbm:g} dBm"
        for device_id, values in sorted(config.devices.items())
    )
    print(
        "Host interaction zone: "
        f"{threshold_summary}; confirmations="
        f"{config.enter_confirmations}/{config.exit_confirmations}; "
        f"stale={config.stale_seconds:g}s",
        flush=True,
    )
    latest_heart_rate: dict[str, HeartRateResult] = {}
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if udp_target else None
    if udp_target:
        print(f"Streaming decoded packets to udp://{udp_target[0]}:{udp_target[1]}")

    with output.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=WIDE_CSV_FIELDS)
        writer.writeheader()
        # Make the live schema visible immediately, even while the gateway is
        # connected but the wearable nodes have not started transmitting yet.
        output_file.flush()

        while True:
            if stop_at is not None and time.monotonic() >= stop_at:
                for ppg_entry, context_entry in row_merger.flush_all():
                    writer.writerow(wide_row(ppg_entry, context_entry))
                output_file.flush()
                if udp_socket is not None:
                    udp_socket.close()
                return
            try:
                with open_gateway_serial(port, baudrate) as connection:
                    print(f"Connected to {port}", flush=True)
                    while True:
                        if stop_at is not None and time.monotonic() >= stop_at:
                            for ppg_entry, context_entry in row_merger.flush_all():
                                writer.writerow(wide_row(ppg_entry, context_entry))
                            output_file.flush()
                            if udp_socket is not None:
                                udp_socket.close()
                            return
                        raw = connection.read(connection.in_waiting or 1)
                        if t5_audio is not None:
                            t5_audio.check_health()
                        if not raw:
                            now = time.monotonic()
                            zone_edge = zone_tracker.poll(now=now)
                            if zone_edge == "stop" and t5_audio is not None:
                                t5_audio.stop_recording()
                            for ppg_entry, context_entry in row_merger.flush_stale(now):
                                writer.writerow(wide_row(ppg_entry, context_entry))
                            if now - last_waiting_message >= 5:
                                waiting_seconds = int(now - last_data_time)
                                print(
                                    "Waiting for gateway data... no serial data for "
                                    f"{waiting_seconds}s",
                                    flush=True,
                                )
                                last_waiting_message = now
                            continue

                        for packet in frame_parser.feed(raw):
                            last_data_time = time.monotonic()
                            zone_edge = zone_tracker.observe(packet)
                            if t5_audio is not None:
                                if zone_edge == "start":
                                    t5_audio.start_recording()
                                elif zone_edge == "stop":
                                    t5_audio.stop_recording()
                            stats, restarted = update_stats(stats_by_stream, packet)
                            if restarted:
                                print(
                                    f"{packet['device_id']} {packet['type']} sequence "
                                    f"restarted at {packet['packet_seq']}",
                                    flush=True,
                                )

                            received_at = (
                                datetime.now()
                                .astimezone()
                                .isoformat(timespec="milliseconds")
                            )
                            if udp_socket is not None and udp_target is not None:
                                stream_packet = packet | {"host_received_ns": time.time_ns()}
                                udp_socket.sendto(
                                    json.dumps(stream_packet, separators=(",", ":")).encode(),
                                    udp_target,
                                )

                            device_id = str(packet["device_id"])
                            heart_rate_result = latest_heart_rate.get(device_id)
                            heart_rate_updated = False
                            if packet["type"] == "raw_ppg_packet" and live_bpm:
                                analyzer = analyzers.get(device_id)
                                if analyzer is None:
                                    analyzer = HeartRateAnalyzer(
                                        int(packet["sample_rate_hz"])
                                    )
                                    analyzers[device_id] = analyzer
                                    display_trackers[device_id] = HeartRateDisplayTracker()
                                analyzer.add_packet(packet)

                            status_key = (
                                str(packet["type"]),
                                str(packet["device_id"]),
                            )
                            status_now = time.monotonic()
                            last_status = last_status_by_stream.get(status_key)
                            if last_status is None or status_now - last_status >= 1.0:
                                last_status_by_stream[status_key] = status_now
                                if packet["type"] == "raw_ppg_packet" and live_bpm:
                                    heart_rate_result = analyzers[
                                        device_id
                                    ].analyze()
                                    latest_heart_rate[device_id] = heart_rate_result
                                    heart_rate_updated = True
                                    display_result = display_trackers[
                                        device_id
                                    ].update(heart_rate_result, now=status_now)
                                    print_packet_status(
                                        packet,
                                        stats,
                                        heart_rate_result,
                                        display_result,
                                    )
                                else:
                                    print_packet_status(
                                        packet,
                                        stats,
                                        host_node_inside=zone_tracker.device_inside(
                                            device_id
                                        ),
                                        interaction_inside=zone_tracker.aggregate_inside,
                                    )

                            entry = BufferedPacket(
                                packet=packet,
                                received_at=received_at,
                                arrived_at=status_now,
                                loss_rate=stats.loss_rate,
                                heart_rate=(
                                    heart_rate_result
                                    if packet["type"] == "raw_ppg_packet"
                                    else None
                                ),
                                heart_rate_updated=heart_rate_updated,
                            )
                            for ppg_entry, context_entry in row_merger.add(entry):
                                writer.writerow(wide_row(ppg_entry, context_entry))

                            now = time.monotonic()
                            if now - last_flush_time >= 1:
                                output_file.flush()
                                last_flush_time = now
            except KeyboardInterrupt:
                for ppg_entry, context_entry in row_merger.flush_all():
                    writer.writerow(wide_row(ppg_entry, context_entry))
                output_file.flush()
                raise
            except serial.SerialException as error:
                print(
                    f"Gateway serial connection lost ({error}); retrying...",
                    flush=True,
                )
                zone_edge = zone_tracker.poll(now=time.monotonic())
                if zone_edge == "stop" and t5_audio is not None:
                    t5_audio.stop_recording()
                time.sleep(1)


def default_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("data") / f"sensor_data_{timestamp}.csv"


def resolve_output_path(requested: Path | None, *, full: bool) -> Path:
    """Choose the installation live CSV for a one-command full session."""
    if requested is not None:
        return requested
    return INTEGRATED_LIVE_OUTPUT if full else default_output_path()


def resolve_audio_output_dir(requested: Path | None, *, full: bool) -> Path:
    if requested is not None:
        return requested
    if full:
        return INTEGRATED_LIVE_OUTPUT.parent / "audio"
    return Path("data")


def default_interaction_config_path(
    directory: Path = Path("interaction_configs"),
) -> Path:
    existing_numbers = [
        int(path.stem)
        for path in directory.glob("*.json")
        if path.stem.isdigit()
    ] if directory.exists() else []
    next_number = max(existing_numbers, default=0) + 1
    return directory / f"{next_number:03d}.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record PPG, IMU, and peer-distance data from the ESP-NOW gateway"
    )
    parser.add_argument(
        "--port",
        help="Gateway serial port, for example COM7. Auto-detected when omitted.",
    )
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Wide CSV path with PPG, IMU/distance, and optional BPM column groups. "
            "With --full, defaults to the live CSV consumed by TouchDesigner."
        ),
    )
    parser.add_argument(
        "--live-bpm",
        action="store_true",
        help="Estimate and display per-device BPM from the raw IR stream.",
    )
    parser.add_argument(
        "--udp",
        type=parse_udp_target,
        metavar="HOST:PORT",
        help="Also stream decoded PPG and context packets as JSON over local UDP.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="Stop cleanly after this many seconds; run until Ctrl+C when omitted.",
    )
    parser.add_argument(
        "--list-ports", action="store_true", help="List serial ports and exit"
    )
    parser.add_argument(
        "--t5-port",
        help="T5AI-Core USB audio/log port; enables zone-controlled WAV recording",
    )
    parser.add_argument(
        "--t5-baudrate",
        type=int,
        default=460800,
        help="T5AI-Core audio UART baud rate (default: 460800)",
    )
    parser.add_argument(
        "--audio-output-dir",
        type=Path,
        default=None,
        help=(
            "Directory for T5 WAV files and audio_events.csv. With --full, "
            "defaults beside the integrated live CSV."
        ),
    )
    parser.add_argument(
        "--interaction-config",
        type=Path,
        help=(
            "Explicitly load per-device host RSSI thresholds from JSON; "
            "when omitted, always use built-in enter=-87/exit=-90 dBm"
        ),
    )
    parser.add_argument(
        "--calibrate-interaction-zone",
        nargs="?",
        const=30.0,
        type=float,
        metavar="SECONDS",
        help=(
            "Stand at the desired boundary and capture its RSSI for SECONDS "
            "(default: 30), save the next numbered config, then start the full session"
        ),
    )
    parser.add_argument(
        "--save-interaction-config",
        type=Path,
        metavar="PATH",
        help=(
            "With --calibrate-interaction-zone, override the default numbered "
            "output path"
        ),
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help=(
            "Run the complete session: auto-detect the S3 gateway and T5 audio "
            "port, save sensor CSV/WAV files, and display live BPM."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.list_ports:
        ports = available_ports()
        print("\n".join(ports) if ports else "No serial ports found")
        return 0

    t5_audio = None
    try:
        port = args.port or detect_esp32_port()
        if args.save_interaction_config is not None and args.calibrate_interaction_zone is None:
            raise ValueError(
                "--save-interaction-config requires --calibrate-interaction-zone"
            )
        if args.interaction_config is not None and args.calibrate_interaction_zone is not None:
            raise ValueError(
                "--interaction-config cannot be combined with "
                "--calibrate-interaction-zone"
            )
        interaction_config = None
        if args.calibrate_interaction_zone is not None:
            save_path = args.save_interaction_config or default_interaction_config_path()
            interaction_config = calibrate_interaction_zone(
                port,
                args.baudrate,
                args.calibrate_interaction_zone,
                save_path,
            )
        else:
            interaction_config = load_interaction_zone_config(args.interaction_config)
        run_full = args.full or args.calibrate_interaction_zone is not None
        output_path = resolve_output_path(args.output, full=args.full)
        audio_output_dir = resolve_audio_output_dir(
            args.audio_output_dir, full=args.full
        )
        t5_port = args.t5_port
        if t5_port is None and run_full:
            try:
                t5_port = detect_t5_audio_port()
            except RuntimeError as error:
                print(
                    f"Warning: T5 audio unavailable ({error}); "
                    "continuing the sensor CSV chain.",
                    file=sys.stderr,
                    flush=True,
                )
        if t5_port:
            t5_audio = start_optional_t5_audio(
                t5_port, audio_output_dir, args.t5_baudrate
            )
        collect(
            port,
            args.baudrate,
            output_path,
            args.udp,
            live_bpm=args.live_bpm or run_full,
            duration_seconds=args.duration,
            t5_audio=t5_audio,
            interaction_config=interaction_config,
        )
    except KeyboardInterrupt:
        print("\nRecording stopped")
        return 0
    except (OSError, RuntimeError, ValueError, serial.SerialException) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        if t5_audio is not None:
            t5_audio.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
