import csv
import math
import tempfile
import unittest
from pathlib import Path

from fit_espnow_ranging import CalibrationSample, build_result


class EspNowRangingFitTests(unittest.TestCase):
    def test_fits_independent_profiles_for_both_receivers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            samples = []
            for distance in (0.5, 1.0, 1.5, 2.0):
                path = Path(directory) / f"{distance}.csv"
                with path.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=(
                            "device_id",
                            "distance_source",
                            "ranging_rssi_raw_dbm",
                        ),
                    )
                    writer.writeheader()
                    for device_id, reference, exponent in (
                        ("person_01", -60.0, 2.0),
                        ("person_40", -65.0, 3.0),
                    ):
                        expected = round(
                            reference - 10.0 * exponent * math.log10(distance)
                        )
                        for offset in (-1, 0, 1):
                            writer.writerow(
                                {
                                    "device_id": device_id,
                                    "distance_source": "espnow_rssi",
                                    "ranging_rssi_raw_dbm": expected + offset,
                                }
                            )
                samples.append(CalibrationSample(distance, path))

            result = build_result(samples, 3)

        self.assertEqual(result["config_version"], 3)
        self.assertEqual(len(result["profiles"]), 2)
        first, second = result["profiles"]
        self.assertEqual(first["local_device_id"], 1)
        self.assertAlmostEqual(first["reference_rssi_at_one_meter_dbm"], -60, delta=0.5)
        self.assertAlmostEqual(first["path_loss_exponent"], 2.0, delta=0.1)
        self.assertGreater(first["r_squared"], 0.99)
        self.assertEqual(second["local_device_id"], 40)
        self.assertAlmostEqual(second["reference_rssi_at_one_meter_dbm"], -65, delta=0.5)
        self.assertAlmostEqual(second["path_loss_exponent"], 3.0, delta=0.1)
        self.assertIn("RANGING_CONFIG_VERSION = 3", result["cpp_configuration"])
        self.assertIn(
            "RANGING_CALIBRATION_MIN_MM = 500", result["cpp_configuration"]
        )
        self.assertIn(
            "RANGING_CALIBRATION_MAX_MM = 2000", result["cpp_configuration"]
        )
        self.assertEqual(result["calibrated_range_m"], [0.5, 2.0])
        self.assertEqual(
            first["sample_statistics_by_distance_m"]["1"]["retained_count"], 3
        )

    def test_rejects_extreme_samples_without_requiring_strict_medians(self) -> None:
        profile = __import__("fit_espnow_ranging").fit_profile(
            {
                0.5: [-70, -70, -71, -20],
                1.0: [-80, -80, -81, -110],
                1.5: [-85, -85, -84, -20],
                2.0: [-85, -85, -86, -110],
            },
            (0.5, 1.0, 1.5, 2.0),
        )

        self.assertGreater(profile["path_loss_exponent"], 0)
        self.assertEqual(
            profile["outlier_filter_by_distance_m"]["0.5"]["rejected_count"], 1
        )
        self.assertEqual(profile["median_rssi_by_distance_m"]["1.5"], -85)
        self.assertEqual(profile["median_rssi_by_distance_m"]["2"], -85)

    def test_rejects_empty_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "no ESP-NOW RSSI samples"):
            build_result([], 1)

    def test_rejects_fewer_than_four_distances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            samples = []
            for distance in (0.5, 1.0, 1.5):
                path = Path(directory) / f"{distance}.csv"
                with path.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=(
                            "device_id",
                            "distance_source",
                            "ranging_rssi_raw_dbm",
                        ),
                    )
                    writer.writeheader()
                    for device_id in ("person_01", "person_40"):
                        writer.writerow(
                            {
                                "device_id": device_id,
                                "distance_source": "espnow_rssi",
                                "ranging_rssi_raw_dbm": -70 - round(distance * 5),
                            }
                        )
                samples.append(CalibrationSample(distance, path))
            with self.assertRaisesRegex(ValueError, "at least 4"):
                build_result(samples, 2)

    def test_rejects_distance_that_disagrees_with_capture_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=(
                        "measured_distance_m",
                        "device_id",
                        "distance_source",
                        "ranging_rssi_raw_dbm",
                    ),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "measured_distance_m": "1.37",
                        "device_id": "person_01",
                        "distance_source": "espnow_rssi",
                        "ranging_rssi_raw_dbm": -80,
                    }
                )

            with self.assertRaisesRegex(ValueError, "records 1.37 m"):
                build_result(
                    [CalibrationSample(distance, path) for distance in (0.5, 1, 2, 3)],
                    3,
                )


if __name__ == "__main__":
    unittest.main()
