import importlib.util
import unittest

import numpy as np

SPEC = importlib.util.spec_from_file_location("sdr_demod", "scripts/sdr_demod.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ResolveModeTests(unittest.TestCase):
    def test_explicit_lsb_is_unchanged_regardless_of_frequency(self):
        self.assertEqual(MODULE.resolve_mode("lsb", 14074), "lsb")

    def test_explicit_usb_is_unchanged_regardless_of_frequency(self):
        self.assertEqual(MODULE.resolve_mode("usb", 3699), "usb")

    def test_auto_below_crossover_resolves_lsb(self):
        self.assertEqual(MODULE.resolve_mode("auto", 3699), "lsb")

    def test_auto_at_crossover_resolves_usb(self):
        self.assertEqual(MODULE.resolve_mode("auto", 10000), "usb")

    def test_auto_above_crossover_resolves_usb(self):
        self.assertEqual(MODULE.resolve_mode("auto", 14074), "usb")

    def test_auto_is_case_insensitive(self):
        self.assertEqual(MODULE.resolve_mode("AUTO", 3699), "lsb")


class DesignShiftedFilterTests(unittest.TestCase):
    def test_rejects_low_cut_not_below_high_cut(self):
        with self.assertRaises(ValueError):
            MODULE.design_shifted_filter(-300, -2700, 128000.0)

    def test_returns_complex_taps_of_requested_length(self):
        taps = MODULE.design_shifted_filter(-2700, -300, 128000.0, numtaps=101)
        self.assertEqual(len(taps), 101)
        self.assertEqual(taps.dtype, np.complex128)

    def test_passes_tone_inside_passband_and_rejects_mirror_image(self):
        # A tone at RF offset -1000 Hz sits inside the LSB passband (-2700..-300);
        # the same tone at +1000 Hz sits in the mirror (USB) image and must be
        # heavily attenuated by an LSB-shaped filter.
        fs = 128000.0
        duration = 0.05
        t = np.arange(int(fs * duration)) / fs
        taps = MODULE.design_shifted_filter(-2700, -300, fs, numtaps=257)

        wanted = np.exp(1j * 2 * np.pi * -1000 * t)
        image = np.exp(1j * 2 * np.pi * 1000 * t)

        # Skip both ramp-up transient (first len(taps) samples) and ramp-down
        # transient (last len(taps) samples) to measure steady-state response
        filtered_wanted = np.convolve(wanted, taps)[len(taps):-len(taps)]
        filtered_image = np.convolve(image, taps)[len(taps):-len(taps)]

        wanted_amplitude = np.max(np.abs(filtered_wanted))
        image_amplitude = np.max(np.abs(filtered_image))
        self.assertGreater(wanted_amplitude, 0.8)
        rejection_db = 20 * np.log10(wanted_amplitude / image_amplitude)
        self.assertGreater(rejection_db, 60)

    def test_rtlsdr_rate_needs_more_taps_for_equivalent_rejection(self):
        # RTL-SDR runs the demod at 256000 Hz (double SDRplay/PlutoSDR's
        # 128000 Hz). design_shifted_filter's transition width scales with
        # sample rate for a fixed tap count, so the same 257 taps used at
        # 128000 Hz give far weaker opposite-sideband rejection here; the
        # backend adapter compensates with NUMTAPS=513 (see
        # scripts/sdr_backends/rtlsdr.py). This test pins that invariant so a
        # future numtaps change can't silently regress selectivity.
        fs = 256000.0
        duration = 0.05
        t = np.arange(int(fs * duration)) / fs
        taps = MODULE.design_shifted_filter(-2700, -300, fs, numtaps=513)

        wanted = np.exp(1j * 2 * np.pi * -1000 * t)
        image = np.exp(1j * 2 * np.pi * 1000 * t)

        filtered_wanted = np.convolve(wanted, taps)[len(taps):-len(taps)]
        filtered_image = np.convolve(image, taps)[len(taps):-len(taps)]

        wanted_amplitude = np.max(np.abs(filtered_wanted))
        image_amplitude = np.max(np.abs(filtered_image))
        self.assertGreater(wanted_amplitude, 0.8)
        rejection_db = 20 * np.log10(wanted_amplitude / image_amplitude)
        self.assertGreater(rejection_db, 60)


class DesignLowpassFilterTests(unittest.TestCase):
    def test_rejects_non_positive_bandwidth(self):
        with self.assertRaises(ValueError):
            MODULE.design_lowpass_filter(0, 128000.0)

    def test_returns_complex_taps_of_requested_length(self):
        taps = MODULE.design_lowpass_filter(16000, 128000.0, numtaps=101)
        self.assertEqual(len(taps), 101)
        self.assertEqual(taps.dtype, np.complex128)

    def test_passes_centered_tone_and_rejects_tone_outside_bandwidth(self):
        fs = 128000.0
        duration = 0.05
        t = np.arange(int(fs * duration)) / fs
        taps = MODULE.design_lowpass_filter(16000, fs, numtaps=257)

        inside = np.exp(1j * 2 * np.pi * 2000 * t)    # within +/-8000 Hz half-bandwidth
        outside = np.exp(1j * 2 * np.pi * 20000 * t)   # well outside

        filtered_inside = np.convolve(inside, taps)[len(taps):-len(taps)]
        filtered_outside = np.convolve(outside, taps)[len(taps):-len(taps)]

        inside_amplitude = np.max(np.abs(filtered_inside))
        outside_amplitude = np.max(np.abs(filtered_outside))
        self.assertGreater(inside_amplitude, 0.8)
        rejection_db = 20 * np.log10(inside_amplitude / outside_amplitude)
        self.assertGreater(rejection_db, 40)

    def test_symmetric_response_matches_positive_and_negative_offset(self):
        fs = 128000.0
        duration = 0.05
        t = np.arange(int(fs * duration)) / fs
        taps = MODULE.design_lowpass_filter(16000, fs, numtaps=257)

        positive = np.exp(1j * 2 * np.pi * 3000 * t)
        negative = np.exp(1j * 2 * np.pi * -3000 * t)

        filtered_positive = np.convolve(positive, taps)[len(taps):-len(taps)]
        filtered_negative = np.convolve(negative, taps)[len(taps):-len(taps)]

        self.assertAlmostEqual(
            np.max(np.abs(filtered_positive)), np.max(np.abs(filtered_negative)), delta=0.01,
        )


class ChooseFmDecimationTests(unittest.TestCase):
    def test_sdrplay_rate_marine_defaults(self):
        stage1, stage2 = MODULE.choose_fm_decimation(128000.0, deviation_hz=5000, channel_bandwidth_hz=16000)
        self.assertEqual(stage1, 4)
        self.assertEqual(stage2, 4)
        self.assertEqual(stage1 * stage2, 16)

    def test_rtlsdr_rate_marine_defaults(self):
        stage1, stage2 = MODULE.choose_fm_decimation(256000.0, deviation_hz=5000, channel_bandwidth_hz=16000)
        self.assertEqual(stage1, 8)
        self.assertEqual(stage2, 4)
        self.assertEqual(stage1 * stage2, 32)

    def test_raises_when_raw_rate_not_multiple_of_target(self):
        with self.assertRaises(ValueError):
            MODULE.choose_fm_decimation(100000.0, deviation_hz=5000, channel_bandwidth_hz=16000)

    def test_raises_when_no_split_meets_intermediate_rate_floor(self):
        with self.assertRaises(ValueError):
            MODULE.choose_fm_decimation(8000.0, deviation_hz=5000, channel_bandwidth_hz=16000)


class FmStreamingDemodulatorTests(unittest.TestCase):
    def test_recovers_audio_tone_frequency(self):
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 4, 4
        audio_freq_hz = 1000.0
        deviation_hz = 3000.0
        duration = 0.5
        t = np.arange(int(raw_iq_rate_hz * duration)) / raw_iq_rate_hz

        # Instantaneous frequency deviation_hz * sin(2*pi*audio_freq_hz*t) is
        # produced by an IQ phase whose derivative equals that expression;
        # -(deviation_hz/audio_freq_hz)*cos(...) is that phase's integral.
        phase = -(deviation_hz / audio_freq_hz) * np.cos(2 * np.pi * audio_freq_hz * t)
        iq = np.exp(1j * phase)

        demod = MODULE.FmStreamingDemodulator(16000, raw_iq_rate_hz, stage1, stage2)
        audio = demod.process(iq)
        audio = audio[len(audio) // 4:]  # drop filter startup transient

        output_rate_hz = raw_iq_rate_hz / (stage1 * stage2)
        spectrum = np.fft.rfft(audio * np.hanning(len(audio)))
        freqs = np.fft.rfftfreq(len(audio), d=1.0 / output_rate_hz)
        peak_freq = freqs[np.argmax(np.abs(spectrum))]
        self.assertAlmostEqual(peak_freq, audio_freq_hz, delta=30.0)

    def test_streaming_chunks_match_single_batch_call(self):
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 4, 4
        audio_freq_hz = 1000.0
        deviation_hz = 3000.0
        duration = 0.3
        t = np.arange(int(raw_iq_rate_hz * duration)) / raw_iq_rate_hz
        phase = -(deviation_hz / audio_freq_hz) * np.cos(2 * np.pi * audio_freq_hz * t)
        iq = np.exp(1j * phase)

        reference = MODULE.FmStreamingDemodulator(16000, raw_iq_rate_hz, stage1, stage2).process(iq)

        chunked = MODULE.FmStreamingDemodulator(16000, raw_iq_rate_hz, stage1, stage2)
        chunk_sizes = [1000, 3333, 500, 7000, 2222, 1]
        pieces = []
        pos = 0
        i = 0
        while pos < len(iq):
            size = chunk_sizes[i % len(chunk_sizes)]
            pieces.append(chunked.process(iq[pos:pos + size]))
            pos += size
            i += 1
        streamed = np.concatenate(pieces)

        n = min(len(reference), len(streamed))
        skip = 20  # filter startup transient
        max_difference = np.max(np.abs(reference[skip:n] - streamed[skip:n]))
        self.assertLess(max_difference, 1e-6)


class FmSquelchTests(unittest.TestCase):
    def test_signal_present_passes_audio(self):
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 4, 4
        t = np.arange(int(raw_iq_rate_hz * 0.1)) / raw_iq_rate_hz
        iq = np.exp(1j * 2 * np.pi * 1000 * t)  # strong, full-amplitude signal

        demod = MODULE.FmStreamingDemodulator(
            16000, raw_iq_rate_hz, stage1, stage2, squelch_db=-20, squelch_hang_ms=50,
        )
        audio = demod.process(iq)
        self.assertGreater(np.max(np.abs(audio[len(audio) // 4:])), 0.0)

    def test_silence_below_threshold_is_squelched(self):
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 4, 4
        rng = np.random.default_rng(1)
        n = int(raw_iq_rate_hz * 0.1)
        iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 1e-4  # very low-power noise

        demod = MODULE.FmStreamingDemodulator(
            16000, raw_iq_rate_hz, stage1, stage2, squelch_db=-20, squelch_hang_ms=50,
        )
        audio = demod.process(iq)
        np.testing.assert_array_equal(audio, np.zeros_like(audio))

    def test_hang_time_keeps_audio_briefly_after_signal_drops(self):
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 4, 4
        n_signal = int(raw_iq_rate_hz * 0.02)
        n_silence = int(raw_iq_rate_hz * 0.02)
        t_signal = np.arange(n_signal) / raw_iq_rate_hz
        signal = np.exp(1j * 2 * np.pi * 1000 * t_signal)
        silence = np.full(n_silence, 1e-6, dtype=np.complex128)

        demod = MODULE.FmStreamingDemodulator(
            16000, raw_iq_rate_hz, stage1, stage2, squelch_db=-20, squelch_hang_ms=100,
        )
        demod.process(signal)
        audio_during_hang = demod.process(silence)
        # hang time (100ms) exceeds the silence chunk's duration (20ms), so
        # squelch should still be open immediately after signal disappears.
        self.assertTrue(demod.squelch_open)
        self.assertTrue(np.any(audio_during_hang != 0.0))


class StreamingDemodulatorTests(unittest.TestCase):
    def test_recovers_correct_frequency_for_lsb(self):
        fs = 128000.0
        decimation = 16
        duration = 0.2
        t = np.arange(int(fs * duration)) / fs
        # LSB: audio tone at 1000 Hz appears at RF offset -1000 Hz.
        iq = np.exp(1j * 2 * np.pi * -1000 * t)

        demod = MODULE.StreamingDemodulator(-2700, -300, fs, decimation)
        audio = demod.process(iq)
        audio = audio[len(audio) // 4:]  # drop filter startup transient

        spectrum = np.fft.rfft(audio * np.hanning(len(audio)))
        freqs = np.fft.rfftfreq(len(audio), d=decimation / fs)
        peak_freq = freqs[np.argmax(np.abs(spectrum))]
        self.assertAlmostEqual(peak_freq, 1000.0, delta=20.0)

    def test_recovers_correct_frequency_for_usb(self):
        fs = 128000.0
        decimation = 16
        duration = 0.2
        t = np.arange(int(fs * duration)) / fs
        # USB: audio tone at 1000 Hz appears at RF offset +1000 Hz.
        iq = np.exp(1j * 2 * np.pi * 1000 * t)

        demod = MODULE.StreamingDemodulator(300, 2700, fs, decimation)
        audio = demod.process(iq)
        audio = audio[len(audio) // 4:]

        spectrum = np.fft.rfft(audio * np.hanning(len(audio)))
        freqs = np.fft.rfftfreq(len(audio), d=decimation / fs)
        peak_freq = freqs[np.argmax(np.abs(spectrum))]
        self.assertAlmostEqual(peak_freq, 1000.0, delta=20.0)

    def test_streaming_chunks_match_single_batch_call(self):
        fs = 128000.0
        decimation = 16
        duration = 0.3
        t = np.arange(int(fs * duration)) / fs
        rng = np.random.default_rng(42)
        iq = np.exp(1j * 2 * np.pi * -1000 * t) + 0.3 * np.exp(1j * 2 * np.pi * 1800 * t)
        iq += (rng.standard_normal(len(t)) + 1j * rng.standard_normal(len(t))) * 0.01

        reference = MODULE.StreamingDemodulator(-2700, -300, fs, decimation).process(iq)

        chunked = MODULE.StreamingDemodulator(-2700, -300, fs, decimation)
        chunk_sizes = [1000, 3333, 500, 7000, 2222, 1]
        pieces = []
        pos = 0
        i = 0
        while pos < len(iq):
            size = chunk_sizes[i % len(chunk_sizes)]
            pieces.append(chunked.process(iq[pos:pos + size]))
            pos += size
            i += 1
        streamed = np.concatenate(pieces)

        n = min(len(reference), len(streamed))
        skip = 200  # filter startup transient
        max_difference = np.max(np.abs(reference[skip:n] - streamed[skip:n]))
        self.assertLess(max_difference, 1e-9)


class AudioToPcm16Tests(unittest.TestCase):
    def test_scales_and_converts_to_int16_bytes(self):
        audio = np.array([0.0, 0.5, -0.5])
        pcm = MODULE.audio_to_pcm16(audio, gain=1.0)
        samples = np.frombuffer(pcm, dtype="<i2")
        np.testing.assert_array_equal(samples, [0, 16383, -16383])

    def test_clips_to_int16_range(self):
        audio = np.array([10.0, -10.0])
        pcm = MODULE.audio_to_pcm16(audio, gain=1.0)
        samples = np.frombuffer(pcm, dtype="<i2")
        np.testing.assert_array_equal(samples, [32767, -32768])


if __name__ == "__main__":
    unittest.main()
