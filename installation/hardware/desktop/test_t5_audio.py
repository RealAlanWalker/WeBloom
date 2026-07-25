import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from t5_audio import AudioFrameParser, HEADER, MAGIC, T5AudioLink, TYPE_DATA, crc16_ccitt


class T5AudioTests(unittest.TestCase):
    def test_parser_recovers_binary_audio_from_mixed_log_stream(self) -> None:
        payload = bytes(range(256)) * 2 + bytes(range(128))
        header = HEADER.pack(
            MAGIC, 1, TYPE_DATA, HEADER.size, 7, 3, 960, 1234,
            len(payload), 0, 2,
        )
        body = header + payload
        frame = body + struct.pack("<H", crc16_ccitt(body))
        parser = AudioFrameParser()
        self.assertEqual(parser.feed(b"ordinary log\r\n" + frame[:100]), [])
        packets = parser.feed(frame[100:] + b"more log")
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["session_id"], 7)
        self.assertEqual(packets[0]["chunk_sequence"], 3)
        self.assertEqual(packets[0]["payload"], payload)
        self.assertEqual(packets[0]["dropped_frames"], 2)

    def test_parser_rejects_bad_crc(self) -> None:
        payload = b"abc"
        body = HEADER.pack(
            MAGIC, 1, TYPE_DATA, HEADER.size, 1, 0, 0, 0,
            len(payload), 0, 0,
        ) + payload
        parser = AudioFrameParser()
        self.assertEqual(parser.feed(body + b"\x00\x00"), [])
        self.assertEqual(parser.crc_errors, 1)

    @patch("t5_audio.threading.Thread")
    @patch("t5_audio.serial.Serial")
    def test_link_enables_interaction_before_querying_status(
        self, serial_factory: MagicMock, thread_factory: MagicMock
    ) -> None:
        connection = serial_factory.return_value
        link = T5AudioLink("COM11", Path("data"))
        link.start()
        self.assertEqual(
            [call.args[0] for call in connection.write.call_args_list],
            [
                b"ap_cmd audio-disable\r",
                b"ap_cmd audio-enable\r",
                b"ap_cmd audio-status\r",
            ],
        )
        thread_factory.return_value.start.assert_called_once_with()

    def test_link_disables_interaction_when_closed(self) -> None:
        link = T5AudioLink("COM11", Path("data"))
        connection = MagicMock()
        link.connection = connection
        link.close()
        connection.write.assert_called_once_with(b"ap_cmd audio-disable\r")

    @patch("t5_audio.time.monotonic", side_effect=[0.0, 0.0, 3.0])
    def test_close_waits_for_end_before_stopping_reader(
        self, monotonic: MagicMock
    ) -> None:
        link = T5AudioLink("COM11", Path("data"))
        connection = MagicMock()
        link.connection = connection
        link.wav_file = MagicMock()
        link.wav_path = Path("data/session.wav")
        link.session_finished = MagicMock()
        link._finish_session = MagicMock()
        link.close()
        link.session_finished.wait.assert_called_once_with(timeout=2.0)
        connection.close.assert_called_once_with()

    def test_empty_session_is_not_published_as_wav(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            link = T5AudioLink("COM11", Path(directory))
            link._begin_session({"session_id": 1})
            path = link.wav_path
            link._finish_session("host_stop", 0)
            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix(".wav.part").exists())

    def test_zero_pcm_session_is_reported_as_fatal(self) -> None:
        link = T5AudioLink("COM11", Path("data"))
        link.connection = MagicMock()
        link.start_recording()
        link.awaiting_first_pcm_since = 0.0
        link._service_first_pcm_timeout(now=10.0)
        with self.assertRaisesRegex(RuntimeError, "no PCM"):
            link.check_health()
        self.assertEqual(link.connection.write.call_count, 1)

    def test_pcm_cancels_zero_frame_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            link = T5AudioLink("COM11", Path(directory))
            link.connection = MagicMock()
            link.start_recording()
            link.awaiting_first_pcm_since = 0.0
            link._begin_session({"session_id": 1})
            link._handle_packet(
                {
                    "type": TYPE_DATA,
                    "session_id": 1,
                    "chunk_sequence": 0,
                    "payload": b"\x00\x00",
                }
            )
            link._service_first_pcm_timeout(now=10.0)
            link.check_health()
            self.assertEqual(link.connection.write.call_count, 1)
            link._finish_session("host_stop", 0)

    def test_late_end_does_not_finish_current_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            link = T5AudioLink("COM11", Path(directory))
            link.recording_requested = True
            link._begin_session({"session_id": 2})
            link._handle_packet(
                {"type": 3, "session_id": 1, "flags": 1, "dropped_frames": 0}
            )
            self.assertEqual(link.session_id, 2)
            link._finish_session("host_stop", 0)


if __name__ == "__main__":
    unittest.main()
