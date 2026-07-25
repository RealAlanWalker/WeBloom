from contextlib import redirect_stderr, redirect_stdout
import csv
import io
import json
import struct
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from collector import (
    CONTEXT_MAGIC,
    CONTEXT_PACKET_STRUCT,
    FRAME_SENSOR_CONTEXT,
    FRAME_SYNC,
    INTEGRATED_LIVE_OUTPUT,
    PPG_PAYLOAD_STRUCT,
    SERIAL_TRAILER_STRUCT,
    SENSOR_FLAG_CLOCK_SYNCED,
    SENSOR_FLAG_DISTANCE_ESPNOW_RSSI,
    SENSOR_FLAG_DISTANCE_EXTRAPOLATED,
    SENSOR_FLAG_DISTANCE_VALID,
    SENSOR_FLAG_IMU_VALID,
    WIDE_CSV_FIELDS,
    BufferedPacket,
    DeviceStats,
    FrameParser,
    HeartRateDisplayTracker,
    InteractionThresholds,
    InteractionZoneConfig,
    InteractionZoneTracker,
    WideRowMerger,
    crc16_ccitt,
    detect_esp32_port,
    detect_t5_audio_port,
    default_interaction_config_path,
    load_interaction_zone_config,
    print_packet_status,
    resolve_audio_output_dir,
    resolve_output_path,
    robust_boundary_rssi,
    start_optional_t5_audio,
    wide_row,
)
from heart_rate import HeartRateResult


def frame_for(payload: bytes) -> bytes:
    return (
        FRAME_SYNC
        + struct.pack("<H", len(payload))
        + payload
        + struct.pack("<H", crc16_ccitt(payload))
    )


class CollectorTests(unittest.TestCase):
    def test_full_defaults_feed_the_integrated_live_chain(self) -> None:
        self.assertEqual(
            resolve_output_path(None, full=True), INTEGRATED_LIVE_OUTPUT
        )
        self.assertEqual(
            resolve_audio_output_dir(None, full=True),
            INTEGRATED_LIVE_OUTPUT.parent / "audio",
        )

    def test_explicit_full_outputs_remain_authoritative(self) -> None:
        sensor_path = Path("chosen") / "sensor.csv"
        audio_path = Path("chosen") / "audio"
        self.assertEqual(
            resolve_output_path(sensor_path, full=True), sensor_path
        )
        self.assertEqual(
            resolve_audio_output_dir(audio_path, full=True), audio_path
        )

    def test_t5_failure_does_not_block_the_sensor_chain(self) -> None:
        with patch("collector.T5AudioLink") as link_type:
            link_type.return_value.start.side_effect = OSError("port busy")
            warning = io.StringIO()
            with redirect_stderr(warning):
                result = start_optional_t5_audio(
                    "COM5", Path("audio"), 460800
                )
        self.assertIsNone(result)
        link_type.return_value.close.assert_called_once_with()
        self.assertIn("continuing the sensor CSV chain", warning.getvalue())

    def test_detects_gateway_by_stable_serial_number(self) -> None:
        class Port:
            def __init__(self, device, vid, pid, serial_number, description=""):
                self.device = device
                self.vid = vid
                self.pid = pid
                self.serial_number = serial_number
                self.description = description

        ports = [
            Port("COM8", 0x303A, 0x1001, "44:B1:76:01:D7:C8"),
            Port("COM7", 0x303A, 0x1001, "D4:05:92:7B:86:04"),
            Port("COM9", 0x303A, 0x1001, "44:B1:76:08:4B:E8"),
        ]
        with patch("collector.list_ports.comports", return_value=ports):
            self.assertEqual(detect_esp32_port(), "COM7")

    def test_detects_t5_serial_b_audio_port(self) -> None:
        class Port:
            def __init__(self, device, description):
                self.device = device
                self.vid = 0x1A86
                self.pid = 0x55D2
                self.description = description

        ports = [
            Port("COM12", "USB-Enhanced-SERIAL-A CH342"),
            Port("COM11", "USB-Enhanced-SERIAL-B CH342"),
        ]
        with patch("collector.list_ports.comports", return_value=ports):
            self.assertEqual(detect_t5_audio_port(), "COM11")

    def make_ppg_frame(self, sample_count: int = 2) -> bytes:
        sample_values = [25000, 26000, 25001, 26002] + [0] * 16
        payload = PPG_PAYLOAD_STRUCT.pack(
            1,
            1,
            0x41565832,
            2,
            1,
            100,
            4,
            40,
            2400,
            sample_count,
            *sample_values,
            3000,
            bytes.fromhex("7C4FAD213E08"),
            5,
            0,
            0,
        )
        return frame_for(payload)

    def make_context_frame(
        self,
        sample_count: int = 2,
        flags: int = SENSOR_FLAG_DISTANCE_VALID
        | SENSOR_FLAG_IMU_VALID
        | SENSOR_FLAG_CLOCK_SYNCED
        | SENSOR_FLAG_DISTANCE_ESPNOW_RSSI
        | SENSOR_FLAG_DISTANCE_EXTRAPOLATED,
        zone_state: int = 1,
        device_id: int = 1,
    ) -> bytes:
        imu_values = [
            16384,
            -8192,
            4096,
            131,
            -262,
            0,
            16000,
            -8000,
            4000,
            130,
            -260,
            10,
        ] + [0] * 18
        context_packet = CONTEXT_PACKET_STRUCT.pack(
            CONTEXT_MAGIC,
            3,
            device_id,
            50,
            7,
            35,
            1000,
            sample_count,
            flags,
            zone_state,
            2,
            40,
            -61,
            -64,
            80,
            7,
            280,
            20,
            325,
            *imu_values,
        )
        trailer = SERIAL_TRAILER_STRUCT.pack(
            1100,
            bytes.fromhex("7C4FAD213E08"),
            8,
            0,
            0,
        )
        payload = bytes((1, FRAME_SENSOR_CONTEXT)) + context_packet + trailer
        return frame_for(payload)

    def test_parses_ppg_packet_without_changing_samples(self) -> None:
        parser = FrameParser()
        frame = self.make_ppg_frame()
        self.assertEqual(parser.feed(b"noise" + frame[:20]), [])
        packets = parser.feed(frame[20:])
        self.assertEqual(len(packets), 1)
        packet = packets[0]
        self.assertEqual(packet["type"], "raw_ppg_packet")
        self.assertEqual(packet["first_sample_seq"], 40)
        self.assertEqual(packet["first_sample_timestamp_ms"], 2400)
        self.assertEqual(packet["samples"][0], [25000, 26000])
        self.assertEqual(packet["samples"][1], [25001, 26002])

    def test_parses_context_packet_without_changing_raw_imu(self) -> None:
        parser = FrameParser()
        packets = parser.feed(self.make_context_frame())
        self.assertEqual(len(packets), 1)
        packet = packets[0]
        self.assertEqual(packet["type"], "sensor_context_packet")
        self.assertTrue(packet["distance_valid"])
        self.assertTrue(packet["imu_valid"])
        self.assertTrue(packet["clock_synced"])
        self.assertTrue(packet["distance_extrapolated"])
        self.assertEqual(packet["zone_name"], "inside")
        self.assertEqual(packet["zone_state"], 1)
        self.assertTrue(packet["within_one_meter"])
        self.assertEqual(packet["range_trend_state"], 2)
        self.assertEqual(packet["range_trend_name"], "approaching")
        self.assertAlmostEqual(packet["range_trend_delta_db"], 3.25)
        self.assertEqual(packet["first_sample_timestamp_ms"], 1000)
        self.assertEqual(packet["samples"][0], [16384, -8192, 4096, 131, -262, 0])
        self.assertEqual(packet["samples"][1], [16000, -8000, 4000, 130, -260, 10])
        self.assertEqual(packet["distance_mm"], 280)
        self.assertEqual(packet["distance_source"], "espnow_rssi")
        self.assertIsNone(packet["ble_rssi_dbm"])
        self.assertEqual(packet["ranging_peer_device_id"], "person_40")
        self.assertEqual(packet["ranging_rssi_raw_dbm"], -61)
        self.assertEqual(packet["ranging_rssi_filtered_dbm"], -64)
        self.assertEqual(packet["ranging_tx_power_qdbm"], 80)
        self.assertEqual(packet["ranging_config_version"], 7)

    def test_parses_outside_zone_state(self) -> None:
        packet = FrameParser().feed(self.make_context_frame(zone_state=0))[0]
        self.assertEqual(packet["zone_state"], 0)
        self.assertEqual(packet["zone_name"], "outside")
        self.assertFalse(packet["within_one_meter"])

    def test_interaction_zone_tracker_requires_both_ranging_directions(self) -> None:
        tracker = InteractionZoneTracker(
            InteractionZoneConfig(
                {
                    "person_01": InteractionThresholds(-87, -90),
                    "person_40": InteractionThresholds(-87, -90),
                },
                enter_confirmations=1,
                exit_confirmations=1,
            )
        )
        packet_1 = {
            "type": "sensor_context_packet",
            "device_id": "person_01",
            "ranging_rssi_filtered_dbm": -95,
        }
        packet_2 = {
            "type": "sensor_context_packet",
            "device_id": "person_40",
            "ranging_rssi_filtered_dbm": -95,
        }
        self.assertIsNone(tracker.observe(packet_1, now=0.0))
        self.assertIsNone(tracker.observe(packet_2, now=0.0))
        packet_1["ranging_rssi_filtered_dbm"] = -85
        self.assertIsNone(tracker.observe(packet_1, now=0.5))
        packet_2["ranging_rssi_filtered_dbm"] = -85
        self.assertEqual(tracker.observe(packet_2, now=0.5), "start")
        packet_1["ranging_rssi_filtered_dbm"] = -92
        self.assertIsNone(tracker.observe(packet_1, now=1.0))
        packet_2["ranging_rssi_filtered_dbm"] = -92
        self.assertEqual(tracker.observe(packet_2, now=1.0), "stop")

    def test_interaction_zone_tracker_stops_when_one_direction_is_stale(self) -> None:
        tracker = InteractionZoneTracker(
            InteractionZoneConfig(
                {
                    "person_01": InteractionThresholds(-87, -90),
                    "person_40": InteractionThresholds(-87, -90),
                },
                enter_confirmations=1,
                exit_confirmations=1,
                stale_seconds=2.5,
            )
        )
        packet_1 = {
            "type": "sensor_context_packet",
            "device_id": "person_01",
            "ranging_rssi_filtered_dbm": -85,
        }
        packet_2 = packet_1 | {"device_id": "person_40"}
        self.assertIsNone(tracker.observe(packet_1, now=0.0))
        self.assertEqual(tracker.observe(packet_2, now=0.0), "start")
        self.assertEqual(tracker.observe(packet_2, now=3.0), "stop")

    def test_stale_stop_requires_real_exit_before_reenter(self) -> None:
        tracker = InteractionZoneTracker(
            InteractionZoneConfig(
                {
                    "person_01": InteractionThresholds(-87, -90),
                    "person_40": InteractionThresholds(-87, -90),
                },
                enter_confirmations=2,
                exit_confirmations=2,
                stale_seconds=2.5,
            )
        )
        packet_1 = {
            "type": "sensor_context_packet",
            "device_id": "person_01",
            "ranging_rssi_filtered_dbm": -85,
        }
        packet_2 = packet_1 | {"device_id": "person_40"}
        self.assertIsNone(tracker.observe(packet_1, now=0.0))
        self.assertIsNone(tracker.observe(packet_2, now=0.0))
        self.assertIsNone(tracker.observe(packet_1, now=0.5))
        self.assertEqual(tracker.observe(packet_2, now=0.5), "start")
        self.assertEqual(tracker.poll(now=3.1), "stop")

        # Fresh inside packets cannot turn an intermittent ranging link into
        # repeated recording sessions.
        self.assertIsNone(tracker.observe(packet_2, now=3.2))
        self.assertIsNone(tracker.observe(packet_2, now=3.7))
        self.assertIsNone(tracker.observe(packet_1, now=3.8))
        self.assertIsNone(tracker.observe(packet_1, now=4.3))

        outside_1 = packet_1 | {"ranging_rssi_filtered_dbm": -92}
        outside_2 = packet_2 | {"ranging_rssi_filtered_dbm": -92}
        self.assertIsNone(tracker.observe(outside_1, now=4.8))
        self.assertIsNone(tracker.observe(outside_2, now=4.9))
        self.assertIsNone(tracker.observe(packet_1, now=5.0))
        self.assertIsNone(tracker.observe(packet_2, now=5.1))
        self.assertIsNone(tracker.observe(packet_1, now=5.2))
        self.assertEqual(tracker.observe(packet_2, now=5.3), "start")

    def test_interaction_zone_tracker_ignores_firmware_zone_state(self) -> None:
        tracker = InteractionZoneTracker()
        packet = {
            "type": "sensor_context_packet",
            "device_id": "person_01",
            "zone_state": 1,
            "ranging_rssi_filtered_dbm": -95,
        }
        for index in range(3):
            self.assertIsNone(tracker.observe(packet, now=float(index)))
        self.assertFalse(tracker.device_inside("person_01"))

    def test_loads_custom_interaction_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "zone.json"
            path.write_text(
                json.dumps(
                    {
                        "devices": {
                            "person_01": {
                                "enter_rssi_dbm": -83,
                                "exit_rssi_dbm": -87,
                            },
                            "person_40": {
                                "enter_rssi_dbm": -84,
                                "exit_rssi_dbm": -88,
                            },
                        },
                        "enter_confirmations": 4,
                        "exit_confirmations": 5,
                        "stale_seconds": 3,
                    }
                ),
                encoding="utf-8",
            )
            config = load_interaction_zone_config(path)

        self.assertEqual(config.devices["person_01"].enter_rssi_dbm, -83)
        self.assertEqual(config.devices["person_40"].exit_rssi_dbm, -88)
        self.assertEqual(config.enter_confirmations, 4)
        self.assertEqual(config.exit_confirmations, 5)
        self.assertEqual(config.stale_seconds, 3)

    def test_omitted_interaction_config_uses_current_defaults(self) -> None:
        config = load_interaction_zone_config(None)
        self.assertEqual(config.devices["person_01"].enter_rssi_dbm, -87)
        self.assertEqual(config.devices["person_40"].exit_rssi_dbm, -90)

    def test_explicit_missing_interaction_config_is_an_error(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_interaction_zone_config(Path("missing-zone-config.json"))


    def test_boundary_calibration_filters_isolated_outliers(self) -> None:
        boundary, retained = robust_boundary_rssi(
            [-88, -87, -87, -86, -87, -20, -110, -87, -88, -86, -87, -87]
        )
        self.assertEqual(boundary, -87)
        self.assertEqual(len(retained), 10)

    def test_interaction_config_uses_next_numeric_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "001.json").write_text("{}", encoding="utf-8")
            (path / "003.json").write_text("{}", encoding="utf-8")
            (path / "notes.json").write_text("{}", encoding="utf-8")
            result = default_interaction_config_path(path)
        self.assertEqual(result.name, "004.json")

    def test_default_interaction_config_uses_dedicated_directory(self) -> None:
        result = default_interaction_config_path()
        self.assertEqual(result.parent, Path("interaction_configs"))
        self.assertTrue(result.stem.isdigit())
        self.assertEqual(len(result.stem), 3)

    def test_invalid_distance_is_blank_in_csv(self) -> None:
        flags = SENSOR_FLAG_IMU_VALID | SENSOR_FLAG_CLOCK_SYNCED
        packet = FrameParser().feed(self.make_context_frame(flags=flags))[0]
        self.assertFalse(packet["distance_valid"])
        row = wide_row(None, BufferedPacket(packet, "now", 1.0, 0.0))
        self.assertEqual(row["distance_mm"], "")
        self.assertEqual(row["distance_valid"], 0)
        self.assertEqual(row["distance_extrapolated"], 0)
        self.assertEqual(row["ranging_rssi_raw_dbm"], -61)
        self.assertEqual(row["ranging_rssi_filtered_dbm"], -64)
        self.assertEqual(row["ble_rssi_dbm"], "")

    def test_parses_mixed_frames_in_one_stream(self) -> None:
        packets = FrameParser().feed(self.make_ppg_frame() + self.make_context_frame())
        self.assertEqual(
            [packet["type"] for packet in packets],
            ["raw_ppg_packet", "sensor_context_packet"],
        )

    def test_rejects_bad_crc_and_recovers_at_next_frame(self) -> None:
        parser = FrameParser()
        damaged = bytearray(self.make_context_frame())
        damaged[20] ^= 0x01
        packets = parser.feed(bytes(damaged) + self.make_ppg_frame())
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["type"], "raw_ppg_packet")
        self.assertEqual(parser.crc_errors, 1)

    def test_rejects_invalid_context_sample_count(self) -> None:
        self.assertEqual(FrameParser().feed(self.make_context_frame(sample_count=0)), [])

    def test_calculates_packet_loss(self) -> None:
        stats = DeviceStats(10, 10)
        stats.update(11)
        stats.update(13)
        self.assertEqual(stats.received, 3)
        self.assertEqual(stats.lost, 1)
        self.assertEqual(stats.loss_rate, 25.0)

    def test_resets_stats_when_sender_sequence_restarts(self) -> None:
        stats = DeviceStats(100, 120, received=21)
        self.assertTrue(stats.update(0))
        self.assertEqual(stats.first_sequence, 0)
        self.assertEqual(stats.last_sequence, 0)
        self.assertEqual(stats.received, 1)

    def test_wide_csv_has_contiguous_sensor_column_groups(self) -> None:
        self.assertEqual(WIDE_CSV_FIELDS[0], "row_received_at")
        for field in (
            "ppg_red_0", "ppg_ir_9", "imu_accel_x_raw_0", "imu_gyro_z_raw_4",
            "distance_mm", "bpm", "context_flags",
            "ranging_peer_device_id", "ranging_rssi_raw_dbm",
            "ranging_rssi_filtered_dbm", "ranging_tx_power_qdbm",
            "ranging_config_version",
            "distance_extrapolated", "within_one_meter",
            "range_trend_state", "range_trend_name", "range_trend_delta_db",
            "ppg_gateway_received_packets", "source_mac",
        ):
            self.assertIn(field, WIDE_CSV_FIELDS)
        self.assertLess(WIDE_CSV_FIELDS.index("ppg_ir_9"), WIDE_CSV_FIELDS.index("imu_accel_x_raw_0"))
        self.assertLess(WIDE_CSV_FIELDS.index("imu_gyro_z_raw_4"), WIDE_CSV_FIELDS.index("distance_mm"))
        self.assertLess(WIDE_CSV_FIELDS.index("distance_mm"), WIDE_CSV_FIELDS.index("bpm"))

    def test_one_wide_row_preserves_ppg_imu_distance_and_bpm(self) -> None:
        ppg_packet = FrameParser().feed(self.make_ppg_frame())[0]
        context_packet = FrameParser().feed(self.make_context_frame())[0]
        context_packet["first_sample_timestamp_ms"] = 2435
        result = HeartRateResult(
            bpm=74.2,
            state="good",
            quality=0.8,
            periodicity=0.9,
            signal_rms=120.0,
            sample_missing_rate=0.0,
            max_gap_ms=0,
            window_end_sample_seq=41,
            window_end_timestamp_ms=2410,
            observed_samples=400,
            missing_samples=0,
        )
        ppg_entry = BufferedPacket(
            ppg_packet, "ppg-now", 1.0, 2.5, result, True
        )
        context_entry = BufferedPacket(
            context_packet, "context-now", 1.01, 3.5
        )
        row = wide_row(ppg_entry, context_entry)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=WIDE_CSV_FIELDS)
        writer.writeheader()
        writer.writerow(row)

        output.seek(0)
        rows = list(csv.DictReader(output))
        self.assertEqual(len(rows), 1)
        saved = rows[0]
        self.assertEqual(saved["ppg_present"], "1")
        self.assertEqual(saved["context_present"], "1")
        self.assertEqual(saved["pair_delta_ms"], "35")
        self.assertEqual(saved["ppg_red_0"], "25000")
        self.assertEqual(saved["ppg_ir_1"], "26002")
        self.assertEqual(saved["imu_accel_x_raw_0"], "16384")
        self.assertEqual(saved["imu_gyro_z_raw_1"], "10")
        self.assertEqual(saved["distance_mm"], "280")
        self.assertEqual(saved["ble_rssi_dbm"], "")
        self.assertEqual(saved["ranging_peer_device_id"], "person_40")
        self.assertEqual(saved["ranging_rssi_raw_dbm"], "-61")
        self.assertEqual(saved["ranging_rssi_filtered_dbm"], "-64")
        self.assertEqual(saved["ranging_tx_power_qdbm"], "80")
        self.assertEqual(saved["ranging_config_version"], "7")
        self.assertEqual(saved["distance_extrapolated"], "1")
        self.assertEqual(saved["within_one_meter"], "1")
        self.assertEqual(saved["range_trend_state"], "2")
        self.assertEqual(saved["range_trend_name"], "approaching")
        self.assertEqual(saved["range_trend_delta_db"], "3.25")
        self.assertEqual(saved["bpm"], "74.20")
        self.assertEqual(saved["heart_rate_updated"], "1")

    def test_merger_pairs_close_packets_and_flushes_missing_groups(self) -> None:
        ppg_packet = FrameParser().feed(self.make_ppg_frame())[0]
        context_packet = FrameParser().feed(self.make_context_frame())[0]
        context_packet["first_sample_timestamp_ms"] = 2435
        merger = WideRowMerger(pair_tolerance_ms=60, flush_after_seconds=0.25)
        self.assertEqual(
            merger.add(BufferedPacket(ppg_packet, "p", 1.0, 0.0)), []
        )
        paired = merger.add(BufferedPacket(context_packet, "c", 1.01, 0.0))
        self.assertEqual(len(paired), 1)
        self.assertIsNotNone(paired[0][0])
        self.assertIsNotNone(paired[0][1])

        later_ppg = dict(ppg_packet)
        later_ppg["packet_seq"] = 5
        later_ppg["first_sample_timestamp_ms"] = 3000
        self.assertEqual(
            merger.add(BufferedPacket(later_ppg, "later", 2.0, 0.0)), []
        )
        stale = merger.flush_stale(2.26)
        self.assertEqual(len(stale), 1)
        self.assertIsNotNone(stale[0][0])
        self.assertIsNone(stale[0][1])

    def test_status_labels_distinguish_ppg_context_and_distance(self) -> None:
        ppg_packet = FrameParser().feed(self.make_ppg_frame())[0]
        context_packet = FrameParser().feed(self.make_context_frame())[0]
        result = HeartRateResult(
            bpm=74.2,
            state="degraded",
            quality=0.68,
            periodicity=0.82,
            signal_rms=100.0,
            sample_missing_rate=17.1,
            max_gap_ms=300,
            window_end_sample_seq=50,
            window_end_timestamp_ms=2500,
            observed_samples=400,
            missing_samples=82,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            print_packet_status(ppg_packet, DeviceStats(1, 1), result)
            print_packet_status(
                context_packet,
                DeviceStats(1, 2, received=1),
                host_node_inside=True,
                interaction_inside=True,
            )
        text = output.getvalue()
        self.assertIn("bpm 74.2", text)
        self.assertIn("sample_missing 17.1%", text)
        self.assertIn("ppg_packet_loss", text)
        self.assertIn("rssi  -64", text)
        self.assertIn("host_node_zone inside", text)
        self.assertIn("interaction_zone inside", text)
        self.assertNotIn("distance ", text)
        self.assertNotIn("within_1m", text)
        self.assertIn("trend approaching", text)
        self.assertIn("context_gap", text)

    def test_display_holds_bpm_for_five_seconds_without_changing_result(self) -> None:
        tracker = HeartRateDisplayTracker()
        trusted = HeartRateResult(
            bpm=82.0,
            state="good",
            quality=0.8,
            periodicity=0.7,
            signal_rms=20.0,
            sample_missing_rate=0.0,
            max_gap_ms=0,
            window_end_sample_seq=800,
            window_end_timestamp_ms=8000,
            observed_samples=800,
            missing_samples=0,
        )
        invalid = HeartRateResult(
            bpm=None,
            state="low_perfusion",
            quality=0.1,
            periodicity=0.0,
            signal_rms=2.0,
            sample_missing_rate=0.0,
            max_gap_ms=0,
            window_end_sample_seq=900,
            window_end_timestamp_ms=9000,
            observed_samples=800,
            missing_samples=0,
        )
        self.assertFalse(tracker.update(trusted, now=10.0).held)
        held = tracker.update(invalid, now=15.0)
        self.assertEqual(held.bpm, 82.0)
        self.assertEqual(held.state, "signal_weak/held")
        self.assertTrue(held.held)
        expired = tracker.update(invalid, now=15.01)
        self.assertIsNone(expired.bpm)
        self.assertEqual(expired.state, "low_perfusion")
        self.assertIsNone(invalid.bpm)

if __name__ == "__main__":
    unittest.main()
