"""T5AI-Core USB audio framing, recording, and control."""

import csv
import struct
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import serial

MAGIC = b"SBA1"
VERSION = 1
TYPE_START = 1
TYPE_DATA = 2
TYPE_END = 3
HEADER = struct.Struct("<4sBBHIIIIHHI")
CRC = struct.Struct("<H")
MAX_PAYLOAD = 640
END_REASONS = {1: "host_stop", 2: "three_minute_timeout", 3: "interaction_disabled"}
FIRST_PCM_TIMEOUT_SECONDS = 3.5


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


class AudioFrameParser:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.crc_errors = 0

    def feed(self, data: bytes) -> list[dict]:
        self.buffer.extend(data)
        packets = []
        while True:
            marker = self.buffer.find(MAGIC)
            if marker < 0:
                del self.buffer[:-3]
                break
            if marker:
                del self.buffer[:marker]
            if len(self.buffer) < HEADER.size:
                break
            values = HEADER.unpack_from(self.buffer)
            _, version, packet_type, header_size, session_id, chunk_sequence, first_sample_sequence, timestamp_ms, payload_length, flags, dropped_frames = values
            if version != VERSION or header_size != HEADER.size or payload_length > MAX_PAYLOAD or packet_type not in (TYPE_START, TYPE_DATA, TYPE_END):
                del self.buffer[0]
                continue
            packet_size = HEADER.size + payload_length + CRC.size
            if len(self.buffer) < packet_size:
                break
            body = bytes(self.buffer[: HEADER.size + payload_length])
            expected_crc = CRC.unpack_from(self.buffer, HEADER.size + payload_length)[0]
            if crc16_ccitt(body) != expected_crc:
                self.crc_errors += 1
                del self.buffer[0]
                continue
            payload = bytes(self.buffer[HEADER.size : HEADER.size + payload_length])
            del self.buffer[:packet_size]
            packets.append({"type": packet_type, "session_id": session_id, "chunk_sequence": chunk_sequence, "first_sample_sequence": first_sample_sequence, "timestamp_ms": timestamp_ms, "flags": flags, "dropped_frames": dropped_frames, "payload": payload})
        return packets


class T5AudioLink:
    def __init__(self, port: str, output_dir: Path, baudrate: int = 460800) -> None:
        self.port = port
        self.output_dir = output_dir
        self.baudrate = baudrate
        self.connection = None
        self.thread = None
        self.stop_event = threading.Event()
        self.parser = AudioFrameParser()
        self.wav_file = None
        self.wav_path = None
        self.wav_temp_path = None
        self.session_id = None
        self.expected_chunk = 0
        self.missing_chunks = 0
        self.lock = threading.Lock()
        self.session_finished = threading.Event()
        self.session_finished.set()
        self.recording_requested = False
        self.awaiting_first_pcm_since = None
        self.fatal_error = None

    def start(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.connection = serial.Serial(self.port, self.baudrate, timeout=0.1)
        self.connection.reset_input_buffer()
        self.thread = threading.Thread(target=self._read_loop, name="t5-audio", daemon=True)
        self.thread.start()
        # Put a previous crashed host session into a known state before arming.
        self.disable_interaction()
        self.enable_interaction()
        self.query_status()

    def close(self) -> None:
        with self.lock:
            self.recording_requested = False
            self.awaiting_first_pcm_since = None
        if self.connection is not None:
            try:
                self.disable_interaction()
            except (OSError, serial.SerialException):
                pass
            self.session_finished.wait(timeout=2.0)
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        with self.lock:
            self._finish_session("receiver_closed", 0)
            if self.connection is not None:
                try:
                    self.connection.close()
                except (OSError, serial.SerialException):
                    pass
                self.connection = None

    def start_recording(self) -> None:
        with self.lock:
            self.recording_requested = True
            self.awaiting_first_pcm_since = time.monotonic()
            self.fatal_error = None
        self._command("audio-start")

    def stop_recording(self) -> None:
        with self.lock:
            self.recording_requested = False
            self.awaiting_first_pcm_since = None
        self._command("audio-stop")

    def query_status(self) -> None:
        self._command("audio-status")

    def enable_interaction(self) -> None:
        self._command("audio-enable")

    def disable_interaction(self) -> None:
        self._command("audio-disable")

    def _command(self, command: str) -> None:
        with self.lock:
            if self.connection is None:
                raise RuntimeError("T5 audio serial port is not open")
            self.connection.write(("ap_cmd " + command + "\r").encode("ascii"))
            self.connection.flush()

    def _read_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                data = self.connection.read(self.connection.in_waiting or 1)
            except serial.SerialException as error:
                with self.lock:
                    self.fatal_error = f"T5 audio serial connection lost: {error}"
                return
            for packet in self.parser.feed(data):
                self._handle_packet(packet)
            self._service_first_pcm_timeout()

    def _service_first_pcm_timeout(self, now: float | None = None) -> None:
        current_time = time.monotonic() if now is None else now
        with self.lock:
            if not self.recording_requested:
                return
            if (
                self.awaiting_first_pcm_since is not None
                and current_time - self.awaiting_first_pcm_since
                >= FIRST_PCM_TIMEOUT_SECONDS
            ):
                self.awaiting_first_pcm_since = None
                self.fatal_error = (
                    "T5 recording started but no PCM arrived within "
                    f"{FIRST_PCM_TIMEOUT_SECONDS:.1f}s"
                )

    def check_health(self) -> None:
        with self.lock:
            if self.fatal_error is not None:
                raise RuntimeError(self.fatal_error)

    def _handle_packet(self, packet: dict) -> None:
        with self.lock:
            if packet["type"] == TYPE_START:
                if self.recording_requested:
                    self._begin_session(packet)
            elif packet["type"] == TYPE_DATA:
                if packet["session_id"] == self.session_id:
                    self.awaiting_first_pcm_since = None
                    self._write_audio(packet)
            elif packet["type"] == TYPE_END:
                if packet["session_id"] == self.session_id:
                    self._finish_session(END_REASONS.get(packet["flags"], f"reason_{packet['flags']}"), packet["dropped_frames"])

    def _begin_session(self, packet: dict) -> None:
        if packet["session_id"] == self.session_id:
            return
        self._finish_session("replaced_by_new_session", 0)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.wav_path = self.output_dir / f"audio_{timestamp}_session{packet['session_id']:03d}.wav"
        self.wav_temp_path = self.wav_path.with_suffix(".wav.part")
        self.wav_file = wave.open(str(self.wav_temp_path), "wb")
        self.wav_file.setnchannels(1)
        self.wav_file.setsampwidth(2)
        self.wav_file.setframerate(16000)
        self.session_id = packet["session_id"]
        self.expected_chunk = 0
        self.missing_chunks = 0
        self.session_finished.clear()
        if self.recording_requested:
            self.awaiting_first_pcm_since = time.monotonic()
        print(f"T5 recording started: {self.wav_path}", flush=True)

    def _write_audio(self, packet: dict) -> None:
        if self.wav_file is None or packet["session_id"] != self.session_id:
            return
        sequence = packet["chunk_sequence"]
        if sequence >= self.expected_chunk:
            self.missing_chunks += sequence - self.expected_chunk
            self.expected_chunk = sequence + 1
        self.wav_file.writeframesraw(packet["payload"])

    def _finish_session(self, reason: str, dropped_frames: int) -> None:
        if self.wav_file is None or self.wav_path is None or self.wav_temp_path is None:
            return
        self.wav_file.close()
        duration_seconds = (self.wav_temp_path.stat().st_size - 44) / 32000.0
        if duration_seconds <= 0:
            self.wav_temp_path.unlink(missing_ok=True)
            print(
                f"T5 recording discarded: no PCM received ({reason})",
                flush=True,
            )
            self.wav_file = None
            self.wav_path = None
            self.wav_temp_path = None
            self.session_id = None
            self.session_finished.set()
            return
        self.wav_temp_path.replace(self.wav_path)
        events_path = self.output_dir / "audio_events.csv"
        write_header = not events_path.exists()
        with events_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if write_header:
                writer.writerow(["saved_at", "session_id", "wav_path", "duration_seconds", "end_reason", "missing_chunks", "device_dropped_frames", "crc_errors"])
            writer.writerow([datetime.now().isoformat(timespec="milliseconds"), self.session_id, self.wav_path.name, f"{duration_seconds:.3f}", reason, self.missing_chunks, dropped_frames, self.parser.crc_errors])
        print(f"T5 recording saved: {self.wav_path} ({duration_seconds:.2f}s, {reason})", flush=True)
        self.wav_file = None
        self.wav_path = None
        self.wav_temp_path = None
        self.session_id = None
        self.session_finished.set()
