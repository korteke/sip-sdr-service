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

    def test_nfm_mode_defaults(self):
        with patch.dict(os.environ, {"SDR_MODE": "nfm"}, clear=True):
            config = MODULE.load_config()
        self.assertEqual(config["mode"], "nfm")
        self.assertEqual(config["deviation_hz"], 5000.0)
        self.assertEqual(config["channel_bandwidth_hz"], 16000.0)
        self.assertIsNone(config["squelch_db"])
        self.assertEqual(config["squelch_hang_ms"], 200.0)
        self.assertIsNone(config["deemphasis_us"])
        self.assertNotIn("low_cut_hz", config)

    def test_nfm_mode_reads_squelch_and_deemphasis(self):
        environment = {
            "SDR_MODE": "nfm",
            "SDR_SQUELCH_DB": "-18",
            "SDR_FM_DEEMPHASIS_US": "6000",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = MODULE.load_config()
        self.assertEqual(config["squelch_db"], -18.0)
        self.assertEqual(config["deemphasis_us"], 6000.0)

    def test_nfm_non_positive_deviation_is_rejected(self):
        with patch.dict(os.environ, {"SDR_MODE": "nfm", "SDR_FM_DEVIATION_HZ": "0"}, clear=True):
            with self.assertRaises(ValueError):
                MODULE.load_config()

    def test_nfm_non_positive_channel_bandwidth_is_rejected(self):
        with patch.dict(os.environ, {"SDR_MODE": "nfm", "SDR_FM_CHANNEL_BANDWIDTH_HZ": "0"}, clear=True):
            with self.assertRaises(ValueError):
                MODULE.load_config()

    def test_nfm_non_positive_squelch_hang_is_rejected(self):
        with patch.dict(os.environ, {"SDR_MODE": "nfm", "SDR_SQUELCH_HANG_MS": "0"}, clear=True):
            with self.assertRaises(ValueError):
                MODULE.load_config()

    def test_nfm_non_positive_deemphasis_is_rejected(self):
        with patch.dict(os.environ, {"SDR_MODE": "nfm", "SDR_FM_DEEMPHASIS_US": "0"}, clear=True):
            with self.assertRaises(ValueError):
                MODULE.load_config()

    def test_wfm_mode_defaults(self):
        with patch.dict(os.environ, {"SDR_MODE": "wfm"}, clear=True):
            config = MODULE.load_config()
        self.assertEqual(config["mode"], "wfm")
        self.assertEqual(config["deviation_hz"], 75000.0)
        self.assertEqual(config["channel_bandwidth_hz"], 200000.0)
        self.assertIsNone(config["squelch_db"])
        self.assertEqual(config["squelch_hang_ms"], 200.0)
        self.assertEqual(config["deemphasis_us"], 50.0)
        self.assertNotIn("low_cut_hz", config)

    def test_wfm_mode_deemphasis_is_overridable(self):
        environment = {"SDR_MODE": "wfm", "SDR_FM_DEEMPHASIS_US": "75"}
        with patch.dict(os.environ, environment, clear=True):
            config = MODULE.load_config()
        self.assertEqual(config["deemphasis_us"], 75.0)

    def test_wfm_mode_deviation_and_bandwidth_are_overridable(self):
        environment = {
            "SDR_MODE": "wfm", "SDR_FM_DEVIATION_HZ": "50000", "SDR_FM_CHANNEL_BANDWIDTH_HZ": "150000",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = MODULE.load_config()
        self.assertEqual(config["deviation_hz"], 50000.0)
        self.assertEqual(config["channel_bandwidth_hz"], 150000.0)

    def test_wfm_non_positive_deviation_is_rejected(self):
        with patch.dict(os.environ, {"SDR_MODE": "wfm", "SDR_FM_DEVIATION_HZ": "0"}, clear=True):
            with self.assertRaises(ValueError):
                MODULE.load_config()

    def test_nfm_mode_defaults_are_unaffected_by_wfm_changes(self):
        with patch.dict(os.environ, {"SDR_MODE": "nfm"}, clear=True):
            config = MODULE.load_config()
        self.assertEqual(config["deviation_hz"], 5000.0)
        self.assertEqual(config["channel_bandwidth_hz"], 16000.0)
        self.assertIsNone(config["deemphasis_us"])

    def test_am_mode_defaults(self):
        with patch.dict(os.environ, {"SDR_MODE": "am"}, clear=True):
            config = MODULE.load_config()
        self.assertEqual(config["mode"], "am")
        self.assertEqual(config["channel_bandwidth_hz"], 25000.0)
        self.assertIsNone(config["squelch_db"])
        self.assertEqual(config["squelch_hang_ms"], 200.0)
        self.assertNotIn("low_cut_hz", config)
        self.assertNotIn("deviation_hz", config)
        self.assertNotIn("deemphasis_us", config)

    def test_am_mode_channel_bandwidth_is_overridable(self):
        environment = {"SDR_MODE": "am", "SDR_AM_CHANNEL_BANDWIDTH_HZ": "8330"}
        with patch.dict(os.environ, environment, clear=True):
            config = MODULE.load_config()
        self.assertEqual(config["channel_bandwidth_hz"], 8330.0)

    def test_am_mode_reads_squelch(self):
        environment = {"SDR_MODE": "am", "SDR_SQUELCH_DB": "-15"}
        with patch.dict(os.environ, environment, clear=True):
            config = MODULE.load_config()
        self.assertEqual(config["squelch_db"], -15.0)

    def test_am_non_positive_channel_bandwidth_is_rejected(self):
        with patch.dict(os.environ, {"SDR_MODE": "am", "SDR_AM_CHANNEL_BANDWIDTH_HZ": "0"}, clear=True):
            with self.assertRaises(ValueError):
                MODULE.load_config()

    def test_am_non_positive_squelch_hang_is_rejected(self):
        with patch.dict(os.environ, {"SDR_MODE": "am", "SDR_SQUELCH_HANG_MS": "0"}, clear=True):
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


class DefaultAudioGainForModeTests(unittest.TestCase):
    def test_fm_modes_default_to_unity_scale_gain(self):
        self.assertEqual(MODULE.default_audio_gain_for_mode("nfm"), 1.0)
        self.assertEqual(MODULE.default_audio_gain_for_mode("wfm"), 1.0)

    def test_non_fm_modes_default_to_the_larger_raw_amplitude_gain(self):
        self.assertEqual(MODULE.default_audio_gain_for_mode("lsb"), 20.0)
        self.assertEqual(MODULE.default_audio_gain_for_mode("usb"), 20.0)
        self.assertEqual(MODULE.default_audio_gain_for_mode("am"), 20.0)


if __name__ == "__main__":
    unittest.main()
