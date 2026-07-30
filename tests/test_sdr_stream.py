import importlib.util
import inspect
import os
import re
import tempfile
import unittest
from pathlib import Path
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


class ResolveSsbCutsTests(unittest.TestCase):
    def test_lsb_returns_negative_high_then_negative_low(self):
        low_cut_hz, high_cut_hz = MODULE.resolve_ssb_cuts("lsb", 300.0, 2700.0)
        self.assertEqual((low_cut_hz, high_cut_hz), (-2700.0, -300.0))

    def test_usb_returns_positive_low_then_positive_high(self):
        low_cut_hz, high_cut_hz = MODULE.resolve_ssb_cuts("usb", -2700.0, -300.0)
        self.assertEqual((low_cut_hz, high_cut_hz), (300.0, 2700.0))

    def test_is_idempotent_regardless_of_input_sign_convention(self):
        first = MODULE.resolve_ssb_cuts("usb", -2700.0, -300.0)
        second = MODULE.resolve_ssb_cuts("usb", *first)
        self.assertEqual(first, second)


class ApplyTunedFrequencyTests(unittest.TestCase):
    def _lsb_config(self):
        with patch.dict(os.environ, {"SDR_MODE": "auto", "SDR_FREQUENCY_KHZ": "3699"}, clear=True):
            config = MODULE.load_config()
        return config

    def test_retune_within_same_sideband_keeps_the_same_demodulator_instance(self):
        config = self._lsb_config()
        original_demodulator = object()
        result = MODULE.apply_tuned_frequency(
            config, 128000.0, 8000.0, MODULE.SSB_MODES, 3799.0, original_demodulator,
        )
        self.assertIs(result, original_demodulator)
        self.assertEqual(config["frequency_khz"], 3799.0)
        self.assertEqual(config["mode"], "lsb")

    def test_retune_crossing_10mhz_boundary_rebuilds_with_flipped_cuts(self):
        config = self._lsb_config()
        original_demodulator = object()
        result = MODULE.apply_tuned_frequency(
            config, 128000.0, 8000.0, MODULE.SSB_MODES, 14074.0, original_demodulator,
        )
        self.assertIsNot(result, original_demodulator)
        self.assertIsInstance(result, MODULE.sdr_demod.StreamingDemodulator)
        self.assertEqual(config["mode"], "usb")
        self.assertEqual(config["low_cut_hz"], 300.0)
        self.assertEqual(config["high_cut_hz"], 2700.0)

    def test_failed_demodulator_rebuild_leaves_config_unchanged(self):
        """A caller retuning across the 10MHz boundary triggers a real
        demodulator rebuild (sdr_demod.build_demodulator). If that rebuild
        raises, config must be left exactly as it was -- not partially
        updated to the new mode/cuts while still holding the old
        demodulator instance, which would desync config from the audio
        actually being produced.
        """
        config = self._lsb_config()
        original_frequency_khz = config["frequency_khz"]
        original_mode = config["mode"]
        original_low_cut_hz = config["low_cut_hz"]
        original_high_cut_hz = config["high_cut_hz"]
        original_demodulator = object()
        with patch.object(MODULE.sdr_demod, "build_demodulator", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                MODULE.apply_tuned_frequency(
                    config, 128000.0, 8000.0, MODULE.SSB_MODES, 14074.0, original_demodulator,
                )
        self.assertEqual(config["frequency_khz"], original_frequency_khz)
        self.assertEqual(config["mode"], original_mode)
        self.assertEqual(config["low_cut_hz"], original_low_cut_hz)
        self.assertEqual(config["high_cut_hz"], original_high_cut_hz)


class ApplyRetuneSafelyTests(unittest.TestCase):
    """apply_retune_safely is main()'s only call site for applying a
    caller's retune. It must never let a hardware rejection (setFrequency
    raising) or a demodulator-rebuild failure escape and kill the shared
    stream for every listener -- see the Important finding in the
    final-review fix-up this covers.
    """

    def _lsb_config(self):
        with patch.dict(os.environ, {"SDR_MODE": "auto", "SDR_FREQUENCY_KHZ": "3699"}, clear=True):
            return MODULE.load_config()

    def test_successful_retune_sets_frequency_writes_current_frequency_and_returns_new_demodulator(self):
        config = self._lsb_config()
        original_demodulator = object()
        set_frequency_calls = []
        with patch.object(MODULE, "write_current_frequency") as write_mock:
            result = MODULE.apply_retune_safely(
                config, 128000.0, 8000.0, MODULE.SSB_MODES, 14074.0, original_demodulator,
                set_frequency_calls.append,
            )
        self.assertIsNot(result, original_demodulator)
        self.assertEqual(set_frequency_calls, [14074000.0])
        write_mock.assert_called_once_with(14074.0)

    def test_hardware_rejection_is_caught_and_keeps_the_previous_demodulator(self):
        """A 3699 (lsb) -> 14074 (usb) retune crosses the sideband boundary,
        so apply_tuned_frequency commits config's mode/cuts/frequency before
        set_frequency_hz ever runs. If set_frequency_hz then rejects the
        frequency, config must be rolled back to its pre-retune state --
        otherwise config would claim usb/14074 while the demodulator we
        keep using (and the hardware, which never actually retuned) are
        still at lsb/3699, a real desync this test pins directly.
        """
        config = self._lsb_config()
        original_frequency_khz = config["frequency_khz"]
        original_mode = config["mode"]
        original_low_cut_hz = config["low_cut_hz"]
        original_high_cut_hz = config["high_cut_hz"]
        original_demodulator = object()

        def rejecting_set_frequency(frequency_hz):
            raise RuntimeError("frequency rejected by hardware")

        with patch.object(MODULE, "write_current_frequency") as write_mock:
            result = MODULE.apply_retune_safely(
                config, 128000.0, 8000.0, MODULE.SSB_MODES, 14074.0, original_demodulator,
                rejecting_set_frequency,
            )
        self.assertIs(result, original_demodulator)
        write_mock.assert_not_called()
        self.assertEqual(config["frequency_khz"], original_frequency_khz)
        self.assertEqual(config["mode"], original_mode)
        self.assertEqual(config["low_cut_hz"], original_low_cut_hz)
        self.assertEqual(config["high_cut_hz"], original_high_cut_hz)

    def test_demodulator_rebuild_failure_is_caught_and_keeps_the_previous_demodulator(self):
        config = self._lsb_config()
        original_mode = config["mode"]
        original_demodulator = object()
        with patch.object(MODULE.sdr_demod, "build_demodulator", side_effect=RuntimeError("boom")):
            with patch.object(MODULE, "write_current_frequency") as write_mock:
                result = MODULE.apply_retune_safely(
                    config, 128000.0, 8000.0, MODULE.SSB_MODES, 14074.0, original_demodulator,
                    lambda frequency_hz: None,
                )
        self.assertIs(result, original_demodulator)
        write_mock.assert_not_called()
        self.assertEqual(config["mode"], original_mode)

    def test_device_none_scenario_is_the_callers_responsibility_not_this_functions(self):
        """apply_retune_safely itself has no opinion about device state --
        main() is responsible for the `device is not None` guard before
        ever calling this (see MainRetuneSafetyTests below). This test
        just documents that a caller who (incorrectly) passes a
        set_frequency_hz that raises AttributeError (as calling
        None.setFrequency(...) would) is still safely caught here, same
        as any other exception.
        """
        config = self._lsb_config()
        original_demodulator = object()

        def none_device_set_frequency(frequency_hz):
            raise AttributeError("'NoneType' object has no attribute 'setFrequency'")

        with patch.object(MODULE, "write_current_frequency") as write_mock:
            result = MODULE.apply_retune_safely(
                config, 128000.0, 8000.0, MODULE.SSB_MODES, 14074.0, original_demodulator,
                none_device_set_frequency,
            )
        self.assertIs(result, original_demodulator)
        write_mock.assert_not_called()


class MainRetuneSafetyTests(unittest.TestCase):
    """Source-level pin for main()'s wiring, since exercising main()'s
    live while-loop needs a faked SoapySDR/backend harness this project
    has never built (see MainReconnectFrequencyTests below for the same
    reasoning). Confirms: (a) the retune block is only entered while a
    device is actually connected -- disconnect() sets device = None via
    a closure without exiting the enclosing `if device is not None:`
    block, so a pending control-file frequency must not reach
    device.setFrequency() on a None device -- and (b) main() applies
    retunes through apply_retune_safely, never a raw, unguarded
    apply_tuned_frequency + device.setFrequency() pair.
    """

    def test_retune_block_is_guarded_by_device_not_none_and_uses_the_safe_wrapper(self):
        source = inspect.getsource(MODULE.main)
        self.assertIn('if device is not None and config["mode"] in SSB_MODES:', source)
        self.assertIn("apply_retune_safely(", source)

    def test_write_current_frequency_is_called_once_at_startup(self):
        source = inspect.getsource(MODULE.main)
        pre_loop_source = source.split("while not stopping", 1)[0]
        self.assertIn('write_current_frequency(config["frequency_khz"])', pre_loop_source)


class MainReconnectFrequencyTests(unittest.TestCase):
    """Regression coverage for a bug where main() cached
    frequency_hz = config["frequency_khz"] * 1000.0 once before the
    while-loop, then reused that stale local for every (re)connect --
    including a reconnect that happens after a caller retunes via
    apply_tuned_frequency, which only ever mutates config in place and has
    no way to reach back into a separate pre-loop local. The result was a
    reconnect (e.g. after an SDR_STALE_SECONDS timeout or a readStream
    exception) silently re-tuning the hardware back to the process's
    original startup frequency while config["mode"]/low_cut_hz/high_cut_hz
    stayed at the retuned sideband -- wrong-sideband, garbled audio.

    Exercising the actual reconnect path end-to-end would need main() to
    run its live while-loop against a faked SoapySDR module, a faked
    backend.open_device, and controlled signal/timing behavior -- test
    infrastructure this project has never built for main() (every existing
    backend test in tests/test_sdr_backends.py deliberately stops short of
    calling open_device() because it touches real hardware). Building that
    harness from scratch is disproportionate to this fix and would be a
    novel, still only lightly-verified simulation of the real failure mode
    -- exactly the situation this project's own "verify against real
    hardware" testing policy exists for. So this test instead pins the
    regression at the source level: the connect block must read
    config["frequency_khz"] fresh on every (re)connect, and no pre-loop
    local caching that value under the name `frequency_hz` may exist to go
    stale again.
    """

    def test_connect_block_reads_current_config_frequency_not_a_stale_local(self):
        source = inspect.getsource(MODULE.main)
        self.assertEqual(
            re.findall(r"\bfrequency_hz\b", source), [],
            "main() must not cache config['frequency_khz'] * 1000.0 in a "
            "pre-loop local named frequency_hz -- read config['frequency_khz'] "
            "directly at connect time instead",
        )
        open_device_call = source.split("backend.open_device(", 1)[1].split(")", 1)[0]
        self.assertIn('config["frequency_khz"] * 1000.0', open_device_call)


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


class ReadControlFrequencyTests(unittest.TestCase):
    def test_missing_file_returns_none_and_unchanged_mtime(self):
        control_path = Path(tempfile.mkdtemp()) / "tune-frequency"
        mtime, frequency_khz = MODULE.read_control_frequency(control_path, 0.0)
        self.assertEqual(mtime, 0.0)
        self.assertIsNone(frequency_khz)

    def test_unchanged_mtime_returns_none(self):
        control_path = Path(tempfile.mkdtemp()) / "tune-frequency"
        control_path.write_text("14074", encoding="utf-8")
        current_mtime = control_path.stat().st_mtime
        mtime, frequency_khz = MODULE.read_control_frequency(control_path, current_mtime)
        self.assertEqual(mtime, current_mtime)
        self.assertIsNone(frequency_khz)

    def test_new_mtime_with_valid_content_returns_parsed_frequency(self):
        control_path = Path(tempfile.mkdtemp()) / "tune-frequency"
        control_path.write_text("14074", encoding="utf-8")
        mtime, frequency_khz = MODULE.read_control_frequency(control_path, 0.0)
        self.assertEqual(frequency_khz, 14074.0)
        self.assertEqual(mtime, control_path.stat().st_mtime)

    def test_new_mtime_with_malformed_content_returns_none_but_advances_mtime(self):
        control_path = Path(tempfile.mkdtemp()) / "tune-frequency"
        control_path.write_text("not-a-number", encoding="utf-8")
        new_mtime = control_path.stat().st_mtime
        mtime, frequency_khz = MODULE.read_control_frequency(control_path, 0.0)
        self.assertIsNone(frequency_khz)
        self.assertEqual(mtime, new_mtime)


class WriteCurrentFrequencyTests(unittest.TestCase):
    def test_creates_parent_directory_and_file(self):
        path = Path(tempfile.mkdtemp()) / "nested" / "current-frequency-khz"
        MODULE.write_current_frequency(3699.0, path)
        self.assertEqual(path.read_text(encoding="utf-8"), "3699")

    def test_overwrite_leaves_no_leftover_temp_file(self):
        path = Path(tempfile.mkdtemp()) / "current-frequency-khz"
        MODULE.write_current_frequency(3699.0, path)
        MODULE.write_current_frequency(14074.0, path)
        self.assertEqual(path.read_text(encoding="utf-8"), "14074")
        self.assertEqual(list(path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
