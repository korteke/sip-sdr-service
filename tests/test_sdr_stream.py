import importlib.util
import os
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("sdr_stream", "scripts/sdr_stream.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LoadConfigTests(unittest.TestCase):
    def test_defaults_are_sdrplay_3699_khz_lsb(self):
        with patch.dict(os.environ, {}, clear=True):
            config = MODULE.load_config()
        self.assertEqual(config["driver"], "sdrplay")
        self.assertEqual(config["frequency_khz"], 3699.0)
        self.assertEqual(config["mode"], "lsb")
        self.assertEqual(config["low_cut_hz"], -2700.0)
        self.assertEqual(config["high_cut_hz"], -300.0)

    def test_driver_selects_the_matching_backend_module(self):
        with patch.dict(os.environ, {"SDR_DRIVER": "rtlsdr"}, clear=True):
            config = MODULE.load_config()
        self.assertEqual(config["driver"], "rtlsdr")
        self.assertEqual(config["backend"].DRIVER_KEY, "rtlsdr")
        self.assertIn("gain_mode", config["backend_config"])

    def test_invalid_driver_is_rejected(self):
        with patch.dict(os.environ, {"SDR_DRIVER": "flightradar"}, clear=True):
            with self.assertRaises(ValueError):
                MODULE.load_config()

    def test_auto_mode_above_crossover_resolves_usb_with_positive_passband(self):
        with patch.dict(os.environ, {"SDR_MODE": "auto", "SDR_FREQUENCY_KHZ": "14074"}, clear=True):
            config = MODULE.load_config()
        self.assertEqual(config["mode"], "usb")
        self.assertEqual(config["low_cut_hz"], 300.0)
        self.assertEqual(config["high_cut_hz"], 2700.0)

    def test_equal_magnitude_cutoffs_are_rejected(self):
        environment = {"SDR_LOW_CUT_HZ": "-300", "SDR_HIGH_CUT_HZ": "300"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ValueError):
                MODULE.load_config()

    def test_non_positive_frequency_is_rejected(self):
        with patch.dict(os.environ, {"SDR_FREQUENCY_KHZ": "0"}, clear=True):
            with self.assertRaises(ValueError):
                MODULE.load_config()

    def test_invalid_mode_is_rejected(self):
        with patch.dict(os.environ, {"SDR_MODE": "fm"}, clear=True):
            with self.assertRaises(ValueError):
                MODULE.load_config()


class BufferTests(unittest.TestCase):
    def test_buffer_below_ceiling_is_unchanged(self):
        buffer = bytearray(8000)
        self.assertEqual(MODULE.resync_buffer(buffer, 16000, 4000), 0)
        self.assertEqual(len(buffer), 8000)

    def test_overflow_resynchronizes_once_to_target(self):
        buffer = bytearray(16170)
        dropped = MODULE.resync_buffer(buffer, 16000, 4000)
        self.assertEqual(dropped, 12170)
        self.assertEqual(len(buffer), 4000)
        self.assertEqual(MODULE.resync_buffer(buffer, 16000, 4000), 0)

    def test_milliseconds_to_bytes_rounds_down_to_even(self):
        self.assertEqual(MODULE.milliseconds_to_bytes(250), 4000)
        self.assertEqual(MODULE.milliseconds_to_bytes(0.0625), 0)


if __name__ == "__main__":
    unittest.main()
