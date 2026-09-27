import unittest

from goad.utils import Utils


class FormatElapsedTimeTest(unittest.TestCase):
    def test_formats_duration_without_timezone_offset(self):
        self.assertEqual(Utils.format_elapsed_time(4759), "01:19:19")

    def test_supports_durations_longer_than_one_day(self):
        self.assertEqual(Utils.format_elapsed_time(90061), "25:01:01")

    def test_truncates_fractional_seconds(self):
        self.assertEqual(Utils.format_elapsed_time(1.9), "00:00:01")


if __name__ == "__main__":
    unittest.main()
