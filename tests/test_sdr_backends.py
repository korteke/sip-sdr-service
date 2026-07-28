import importlib.util
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, "scripts")

SDRPLAY_SPEC = importlib.util.spec_from_file_location("sdr_backends.sdrplay", "scripts/sdr_backends/sdrplay.py")
SDRPLAY = importlib.util.module_from_spec(SDRPLAY_SPEC)
SDRPLAY_SPEC.loader.exec_module(SDRPLAY)


class SdrplayBackendConfigTests(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            config = SDRPLAY.load_backend_config()
        self.assertEqual(config["antenna"], "a")
        self.assertEqual(config["gain_mode"], "agc")
        self.assertEqual(config["gain_reduction_db"], 40.0)
        self.assertEqual(config["lna_state"], 0.0)
        self.assertFalse(config["bias_t"])

    def test_invalid_antenna_is_rejected(self):
        with patch.dict(os.environ, {"SDRPLAY_ANTENNA": "c"}, clear=True):
            with self.assertRaises(ValueError):
                SDRPLAY.load_backend_config()

    def test_invalid_gain_mode_is_rejected(self):
        with patch.dict(os.environ, {"SDRPLAY_GAIN_MODE": "turbo"}, clear=True):
            with self.assertRaises(ValueError):
                SDRPLAY.load_backend_config()

    def test_bias_t_on_is_recognized(self):
        with patch.dict(os.environ, {"SDRPLAY_BIAS_T": "on"}, clear=True):
            config = SDRPLAY.load_backend_config()
        self.assertTrue(config["bias_t"])

    def test_driver_key_and_rate_arithmetic(self):
        self.assertEqual(SDRPLAY.DRIVER_KEY, "sdrplay")
        self.assertEqual(SDRPLAY.IQ_SAMPLE_RATE_HZ / SDRPLAY.SOFTWARE_DECIMATION, 8000.0)


RTLSDR_SPEC = importlib.util.spec_from_file_location("sdr_backends.rtlsdr", "scripts/sdr_backends/rtlsdr.py")
RTLSDR = importlib.util.module_from_spec(RTLSDR_SPEC)
RTLSDR_SPEC.loader.exec_module(RTLSDR)


class RtlsdrBackendConfigTests(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            config = RTLSDR.load_backend_config()
        self.assertEqual(config["gain_mode"], "agc")
        self.assertEqual(config["gain_db"], 30.0)

    def test_invalid_gain_mode_is_rejected(self):
        with patch.dict(os.environ, {"RTLSDR_GAIN_MODE": "turbo"}, clear=True):
            with self.assertRaises(ValueError):
                RTLSDR.load_backend_config()

    def test_manual_gain_is_read(self):
        with patch.dict(os.environ, {"RTLSDR_GAIN_MODE": "manual", "RTLSDR_GAIN_DB": "42.5"}, clear=True):
            config = RTLSDR.load_backend_config()
        self.assertEqual(config["gain_mode"], "manual")
        self.assertEqual(config["gain_db"], 42.5)

    def test_driver_key_and_rate_arithmetic(self):
        self.assertEqual(RTLSDR.DRIVER_KEY, "rtlsdr")
        self.assertEqual(RTLSDR.DEVICE_SAMPLE_RATE_HZ / RTLSDR.SOFTWARE_DECIMATION, 8000.0)


PLUTOSDR_SPEC = importlib.util.spec_from_file_location("sdr_backends.plutosdr", "scripts/sdr_backends/plutosdr.py")
PLUTOSDR = importlib.util.module_from_spec(PLUTOSDR_SPEC)
PLUTOSDR_SPEC.loader.exec_module(PLUTOSDR)


class PlutosdrBackendConfigTests(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            config = PLUTOSDR.load_backend_config()
        self.assertEqual(config["gain_mode"], "agc")
        self.assertEqual(config["gain_db"], 30.0)

    def test_invalid_gain_mode_is_rejected(self):
        with patch.dict(os.environ, {"PLUTOSDR_GAIN_MODE": "turbo"}, clear=True):
            with self.assertRaises(ValueError):
                PLUTOSDR.load_backend_config()

    def test_manual_gain_is_read(self):
        with patch.dict(os.environ, {"PLUTOSDR_GAIN_MODE": "manual", "PLUTOSDR_GAIN_DB": "15"}, clear=True):
            config = PLUTOSDR.load_backend_config()
        self.assertEqual(config["gain_mode"], "manual")
        self.assertEqual(config["gain_db"], 15.0)

    def test_driver_key_and_rate_arithmetic(self):
        self.assertEqual(PLUTOSDR.DRIVER_KEY, "plutosdr")
        self.assertEqual(PLUTOSDR.DEVICE_SAMPLE_RATE_HZ / PLUTOSDR.SOFTWARE_DECIMATION, 8000.0)


if __name__ == "__main__":
    unittest.main()
