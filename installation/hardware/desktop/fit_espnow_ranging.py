import argparse
import csv
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


MIN_CALIBRATION_DISTANCES = 4
OUTLIER_MAD_SCALE = 3.0
MIN_OUTLIER_THRESHOLD_DB = 3.0


@dataclass(frozen=True)
class CalibrationSample:
    distance_m: float
    path: Path


def parse_sample(value: str) -> CalibrationSample:
    distance_text, separator, path_text = value.partition("=")
    if not separator or not path_text:
        raise argparse.ArgumentTypeError("use DISTANCE_M=CSV_PATH")
    try:
        distance_m = float(distance_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("distance must be a number") from error
    if distance_m <= 0:
        raise argparse.ArgumentTypeError("distance must be greater than zero")
    return CalibrationSample(distance_m, Path(path_text))


def device_number(device_id: str) -> int:
    prefix = "person_"
    if not device_id.startswith(prefix):
        raise ValueError(f"unexpected device_id {device_id!r}")
    return int(device_id[len(prefix) :])


def load_samples(
    samples: list[CalibrationSample],
) -> dict[str, dict[float, list[int]]]:
    grouped: dict[str, dict[float, list[int]]] = {}
    for sample in samples:
        with sample.path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            required_fields = {
                "device_id",
                "distance_source",
                "ranging_rssi_raw_dbm",
            }
            missing = required_fields.difference(reader.fieldnames or [])
            if missing:
                raise ValueError(
                    f"{sample.path} is missing columns: {', '.join(sorted(missing))}"
                )
            for row in reader:
                measured_distance = row.get("measured_distance_m", "").strip()
                if measured_distance and not math.isclose(
                    float(measured_distance), sample.distance_m, abs_tol=0.0005
                ):
                    raise ValueError(
                        f"{sample.path} records {measured_distance} m but was "
                        f"provided as {sample.distance_m:g} m"
                    )
                if row["distance_source"] != "espnow_rssi":
                    continue
                rssi_text = row["ranging_rssi_raw_dbm"].strip()
                if not rssi_text:
                    continue
                rssi_dbm = int(rssi_text)
                if not -110 <= rssi_dbm <= -10:
                    continue
                grouped.setdefault(row["device_id"], {}).setdefault(
                    sample.distance_m, []
                ).append(rssi_dbm)
    return grouped


def reject_rssi_outliers(values: list[int]) -> tuple[list[int], dict]:
    """Apply a Hampel filter without hiding the original sample statistics."""
    center = float(statistics.median(values))
    absolute_deviations = [abs(value - center) for value in values]
    mad_db = float(statistics.median(absolute_deviations))
    robust_sigma_db = 1.4826 * mad_db
    threshold_db = max(MIN_OUTLIER_THRESHOLD_DB, OUTLIER_MAD_SCALE * robust_sigma_db)
    retained = [value for value in values if abs(value - center) <= threshold_db]
    if len(retained) < max(3, len(values) // 2):
        raise ValueError("outlier filtering retained too few RSSI samples")
    return retained, {
        "method": "Hampel median/MAD",
        "center_dbm": center,
        "mad_db": mad_db,
        "threshold_db": round(threshold_db, 3),
        "original_count": len(values),
        "retained_count": len(retained),
        "rejected_count": len(values) - len(retained),
    }


def theil_sen_fit(x_values: list[float], y_values: list[float]) -> tuple[float, float]:
    slopes = [
        (y_values[right] - y_values[left])
        / (x_values[right] - x_values[left])
        for left in range(len(x_values))
        for right in range(left + 1, len(x_values))
    ]
    slope = float(statistics.median(slopes))
    intercept = float(
        statistics.median(
            y_value - slope * x_value
            for x_value, y_value in zip(x_values, y_values)
        )
    )
    return intercept, slope


def fit_profile(
    distance_samples: dict[float, list[int]],
    required_distances: tuple[float, ...],
) -> dict:
    missing = [
        distance
        for distance in required_distances
        if distance not in distance_samples or not distance_samples[distance]
    ]
    if missing:
        raise ValueError(
            "missing required distances: " + ", ".join(f"{value:g} m" for value in missing)
        )

    retained_by_distance = {}
    filtering_by_distance = {}
    for distance in required_distances:
        retained, filtering = reject_rssi_outliers(distance_samples[distance])
        retained_by_distance[distance] = retained
        filtering_by_distance[distance] = filtering
    point_medians = {
        distance: float(statistics.median(retained_by_distance[distance]))
        for distance in required_distances
    }
    y_values = [point_medians[distance] for distance in required_distances]
    x_values = [math.log10(distance) for distance in required_distances]
    y_mean = statistics.mean(y_values)
    intercept, slope = theil_sen_fit(x_values, y_values)
    exponent = -slope / 10.0
    if exponent <= 0:
        raise ValueError("RSSI does not decrease with distance; calibration rejected")
    residuals = [
        y_value - (intercept + slope * x_value)
        for x_value, y_value in zip(x_values, y_values)
    ]
    rmse_db = math.sqrt(statistics.mean(value * value for value in residuals))
    total_variance = sum((value - y_mean) ** 2 for value in y_values)
    r_squared = 1.0 - sum(value * value for value in residuals) / total_variance
    return {
        "reference_rssi_at_one_meter_dbm": round(intercept, 3),
        "path_loss_exponent": round(exponent, 4),
        "rmse_db": round(rmse_db, 3),
        "r_squared": round(r_squared, 5),
        "fit_method": "Theil-Sen on Hampel-filtered per-distance medians",
        "median_rssi_by_distance_m": {
            f"{distance:g}": point_medians[distance]
            for distance in required_distances
        },
        "sample_count_by_distance_m": {
            f"{distance:g}": len(retained_by_distance[distance])
            for distance in required_distances
        },
        "outlier_filter_by_distance_m": {
            f"{distance:g}": filtering_by_distance[distance]
            for distance in required_distances
        },
        "sample_statistics_by_distance_m": {
            f"{distance:g}": {
                "original_count": len(distance_samples[distance]),
                "retained_count": len(retained_by_distance[distance]),
                "rejected_count": (
                    len(distance_samples[distance])
                    - len(retained_by_distance[distance])
                ),
                "median_dbm": point_medians[distance],
                "mean_dbm": round(statistics.mean(retained_by_distance[distance]), 3),
                "standard_deviation_db": round(
                    statistics.stdev(retained_by_distance[distance])
                    if len(retained_by_distance[distance]) > 1
                    else 0.0,
                    3,
                ),
                "min_dbm": min(retained_by_distance[distance]),
                "max_dbm": max(retained_by_distance[distance]),
            }
            for distance in required_distances
        },
    }


def build_result(samples: list[CalibrationSample], config_version: int) -> dict:
    if config_version <= 0:
        raise ValueError("config_version must be greater than zero")
    grouped = load_samples(samples)
    if not grouped:
        raise ValueError("no ESP-NOW RSSI samples found")
    required_distances = tuple(sorted({sample.distance_m for sample in samples}))
    if len(required_distances) < MIN_CALIBRATION_DISTANCES:
        raise ValueError(
            f"calibration requires at least {MIN_CALIBRATION_DISTANCES} "
            "distinct distances"
        )

    profiles = []
    for device_id in sorted(grouped, key=device_number):
        profile = fit_profile(grouped[device_id], required_distances)
        profile["local_device_id"] = device_number(device_id)
        profiles.append(profile)
    if {profile["local_device_id"] for profile in profiles} != {1, 40}:
        raise ValueError("calibration requires both person_01 and person_40")

    cpp_profiles = [
        "// BEGIN GENERATED RANGING CONFIG",
        f"constexpr uint16_t RANGING_CALIBRATION_MIN_MM = {round(required_distances[0] * 1000)};",
        f"constexpr uint16_t RANGING_CALIBRATION_MAX_MM = {round(required_distances[-1] * 1000)};",
        f"constexpr uint16_t RANGING_CONFIG_VERSION = {config_version};",
        "constexpr RangingCalibration RANGING_CALIBRATIONS[] = {",
    ]
    for profile in profiles:
        cpp_profiles.append(
            "    "
            f"{{{profile['local_device_id']}, true, "
            f"{profile['reference_rssi_at_one_meter_dbm']:.3f}f, "
            f"{profile['path_loss_exponent']:.4f}f}},"
        )
    cpp_profiles.extend(("};", "// END GENERATED RANGING CONFIG"))

    return {
        "config_version": config_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": "RSSI(d)=A-10*n*log10(d)",
        "required_distances_m": list(required_distances),
        "calibrated_range_m": [required_distances[0], required_distances[-1]],
        "profiles": profiles,
        "cpp_configuration": "\n".join(cpp_profiles),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit per-receiver ESP-NOW RSSI distance profiles."
    )
    parser.add_argument(
        "--sample",
        action="append",
        type=parse_sample,
        required=True,
        help=(
            "calibration capture in DISTANCE_M=CSV_PATH form; repeat for at "
            "least four distinct distances"
        ),
    )
    parser.add_argument("--config-version", type=int, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = build_result(args.sample, args.config_version)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
