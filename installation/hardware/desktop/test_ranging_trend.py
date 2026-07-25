import unittest
from pathlib import Path

from ranging_trend import load_filtered_context_series, replay_events


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA = PROJECT_ROOT / "data"


class RangingTrendReplayTests(unittest.TestCase):
    def test_static_calibration_has_at_most_two_direction_entries_per_minute(self) -> None:
        paths = (
            DATA / "espnow_calibration_0p5m.csv",
            DATA / "espnow_calibration_1p0m.csv",
            DATA / "espnow_calibration_1p5m.csv",
            DATA / "espnow_calibration_2p0m.csv",
        )
        for path in paths:
            if not path.exists():
                self.skipTest("local recorded ranging fixtures are not available")
            for device_id in ("person_01", "person_40"):
                events = replay_events(
                    load_filtered_context_series(path, device_id),
                    discard_initial_s=5.0 if "0p5" in path.name else 0.0,
                )
                directional = [
                    event
                    for event in events
                    if event.state in ("approaching", "receding")
                ]
                self.assertLessEqual(
                    len(directional),
                    2,
                    f"{path.name} {device_id}: {directional}",
                )

    def test_recorded_motion_detects_both_directions_within_3p5_seconds(self) -> None:
        path = DATA / "sensor_data_20260723_205611.csv"
        if not path.exists():
            self.skipTest("local recorded ranging fixture is not available")
        events = replay_events(load_filtered_context_series(path, "person_01"))
        approaching = [event for event in events if event.state == "approaching"]
        receding = [event for event in events if event.state == "receding"]
        self.assertTrue(approaching, events)
        self.assertTrue(receding, events)
        # The recorded movement starts near t=45 s (approach) and t=6 s (recede).
        self.assertLessEqual(min(event.time_s for event in approaching) - 45.0, 3.5)
        self.assertLessEqual(min(event.time_s for event in receding) - 6.0, 3.5)


if __name__ == "__main__":
    unittest.main()
