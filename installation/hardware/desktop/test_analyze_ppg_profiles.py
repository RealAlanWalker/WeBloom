import unittest

from analyze_ppg_profiles import ProfileEvaluation, choose_profile


def evaluation(
    profile_id: int, led: int, availability: float, *, passed: bool = True
) -> ProfileEvaluation:
    return ProfileEvaluation(
        profile_id=profile_id,
        adc_range=16384,
        led_amplitude=led,
        raw_minimum=10000,
        raw_maximum=100000,
        saturation_samples=0,
        valid_results=18,
        evaluated_updates=20,
        availability=availability,
        compared_results=18,
        within_five_results=17,
        within_five_rate=17 / 18,
        maximum_error_bpm=7.0,
        passed=passed,
    )


class PpgProfileAnalysisTests(unittest.TestCase):
    def test_selects_lowest_led_power_within_five_percent_of_best(self) -> None:
        selected = choose_profile(
            [
                evaluation(0, 0x7F, 0.95),
                evaluation(1, 0x40, 0.91),
                evaluation(2, 0x60, 0.89),
            ]
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.profile_id, 1)

    def test_no_passing_profile_requests_baseline_fallback(self) -> None:
        self.assertIsNone(
            choose_profile([evaluation(0, 0x7F, 0.95, passed=False)])
        )


if __name__ == "__main__":
    unittest.main()
