import importlib.util
import unittest

import numpy as np
from scipy.signal import firwin, lfilter

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

    def test_wfm_rate_needs_fewer_taps_for_equivalent_rejection(self):
        # WFM runs the channel filter at 512000 Hz with a 200000 Hz channel
        # bandwidth (100000 Hz half-bandwidth) -- a much wider channel
        # relative to its sample rate (512000/200000 ~= 2.6) than NFM's at
        # 128000 Hz (128000/16000 = 8). This test pins the empirically-
        # verified result that WFM needs *fewer* taps than NFM's 257 for
        # equivalent adjacent-channel rejection, not more, despite the 4x
        # higher sample rate -- see scripts/sdr_backends/plutosdr.py's
        # WFM_NUMTAPS=129.
        fs = 512000.0
        duration = 0.02
        t = np.arange(int(fs * duration)) / fs
        taps = MODULE.design_lowpass_filter(200000, fs, numtaps=129)

        inside = np.exp(1j * 2 * np.pi * 60000 * t)    # well within the 100000 Hz half-bandwidth
        outside = np.exp(1j * 2 * np.pi * 110000 * t)  # just past the passband edge

        filtered_inside = np.convolve(inside, taps)[len(taps):-len(taps)]
        filtered_outside = np.convolve(outside, taps)[len(taps):-len(taps)]

        inside_amplitude = np.max(np.abs(filtered_inside))
        outside_amplitude = np.max(np.abs(filtered_outside))
        self.assertGreater(inside_amplitude, 0.8)
        rejection_db = 20 * np.log10(inside_amplitude / outside_amplitude)
        self.assertGreater(rejection_db, 60)


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

    def test_tolerates_real_hardware_clock_imprecision(self):
        # Real PlutoSDR hardware asked for 128000 Hz commonly reports back
        # 127999 Hz (~8 ppm off) due to PLL/crystal clock quantization, not a
        # misconfiguration - this must still resolve the same as an exact
        # 128000 Hz would.
        stage1, stage2 = MODULE.choose_fm_decimation(127999.0, deviation_hz=5000, channel_bandwidth_hz=16000)
        self.assertEqual(stage1, 4)
        self.assertEqual(stage2, 4)

    def test_still_rejects_a_rate_nowhere_near_a_multiple(self):
        with self.assertRaises(ValueError):
            MODULE.choose_fm_decimation(128500.0, deviation_hz=5000, channel_bandwidth_hz=16000)


class MatchesRateMultipleTests(unittest.TestCase):
    def test_exact_multiple(self):
        self.assertEqual(MODULE.matches_rate_multiple(128000.0, 8000), 16)

    def test_within_tolerance_of_multiple(self):
        self.assertEqual(MODULE.matches_rate_multiple(127999.0, 8000), 16)

    def test_far_from_any_multiple_returns_none(self):
        self.assertIsNone(MODULE.matches_rate_multiple(100000.0, 8000))

    def test_close_to_zero_returns_none(self):
        self.assertIsNone(MODULE.matches_rate_multiple(1.0, 8000))


class SplitAudioDecimationTests(unittest.TestCase):
    def test_small_ratios_are_left_unsplit(self):
        # Every decimation ratio the already-shipped modes actually use
        # (nfm's stage2=4 on all three backends, and anything smaller) must
        # stay a single stage, so their filtering is numerically untouched.
        for total in (1, 2, 3, 4):
            self.assertEqual(MODULE.split_audio_decimation(total), [total])

    def test_wfm_ratio_is_split_with_the_smallest_final_substage(self):
        # The last sub-stage's lowpass is the only one that has to be sharp
        # right at the output Nyquist, so it gets the smallest ratio.
        self.assertEqual(MODULE.split_audio_decimation(64), [8, 4, 2])

    def test_split_always_multiplies_back_to_the_total(self):
        for total in range(1, 200):
            ratios = MODULE.split_audio_decimation(total)
            self.assertEqual(int(np.prod(ratios)), total, f"bad split for {total}: {ratios}")

    def test_unfactorable_total_falls_back_to_a_single_stage(self):
        # A large prime has no usable factorization; a single stage is the
        # only option, and must not hang or raise.
        self.assertEqual(MODULE.split_audio_decimation(101), [101])


class CascadedAudioDecimatorTests(unittest.TestCase):
    def test_single_stage_matches_a_plain_lowpass_and_decimate(self):
        # Pins the collapse-to-one-stage property that keeps nfm bit-exact.
        rate_hz = 32000.0
        decimation = 4
        rng = np.random.default_rng(3)
        samples = rng.standard_normal(4000)

        cutoff_hz = MODULE.AUDIO_CUTOFF_MARGIN * rate_hz / (2 * decimation)
        taps = firwin(129, cutoff_hz, fs=rate_hz, window=("kaiser", 8.0))
        expected = lfilter(taps, [1.0], samples)[::decimation]

        actual = MODULE.CascadedAudioDecimator(rate_hz, decimation, numtaps=129).process(samples)
        np.testing.assert_array_equal(actual, expected)

    def test_streaming_chunks_match_single_batch_call(self):
        rate_hz = 512000.0
        rng = np.random.default_rng(11)
        samples = rng.standard_normal(60000)

        reference = MODULE.CascadedAudioDecimator(rate_hz, 64, numtaps=129).process(samples)

        chunked = MODULE.CascadedAudioDecimator(rate_hz, 64, numtaps=129)
        chunk_sizes = [1000, 3333, 500, 7000, 2222, 1]
        pieces = []
        pos = 0
        i = 0
        while pos < len(samples):
            size = chunk_sizes[i % len(chunk_sizes)]
            pieces.append(chunked.process(samples[pos:pos + size]))
            pos += size
            i += 1
        streamed = np.concatenate(pieces)

        n = min(len(reference), len(streamed))
        self.assertGreater(n, 800)
        np.testing.assert_allclose(reference[:n], streamed[:n], atol=1e-9)

    def test_output_rate_and_final_cutoff_target_the_audio_band(self):
        cascade = MODULE.CascadedAudioDecimator(512000.0, 64, numtaps=129)
        self.assertEqual(cascade.output_rate_hz, 8000.0)
        # The last sub-stage -- and only the last -- enforces the real audio
        # bandwidth; the earlier ones sit far above it.
        self.assertAlmostEqual(cascade.cutoffs_hz[-1], MODULE.AUDIO_CUTOFF_MARGIN * 8000.0 / 2)
        for cutoff_hz in cascade.cutoffs_hz[:-1]:
            self.assertGreater(cutoff_hz, 4000.0)


class FmStreamingDemodulatorTests(unittest.TestCase):
    def test_wfm_rejects_audio_above_the_output_nyquist(self):
        # Regression test for the original single-stage stage-2 audio filter:
        # at wfm's real parameters choose_fm_decimation picks stage1=1,
        # stage2=64, so one 129-tap FIR at 512000 Hz had to realize a 3600 Hz
        # cutoff on its own. It couldn't -- broadcast FM audio above the 8kHz
        # output's 4kHz Nyquist came through only ~3.4dB down and folded
        # audibly back into the output band. The cascaded decimator measures
        # about -95dB here; 60dB is a deliberately loose floor over that.
        raw_iq_rate_hz = 512000.0
        stage1, stage2 = MODULE.choose_fm_decimation(raw_iq_rate_hz, 75000, 200000)
        self.assertEqual((stage1, stage2), (1, 64))
        deviation_hz = 75000.0
        duration = 0.25
        t = np.arange(int(raw_iq_rate_hz * duration)) / raw_iq_rate_hz

        def peak_amplitude(audio_freq_hz):
            phase = -(deviation_hz / audio_freq_hz) * np.cos(2 * np.pi * audio_freq_hz * t)
            demod = MODULE.FmStreamingDemodulator(
                200000, raw_iq_rate_hz, stage1, stage2, deviation_hz, numtaps=129,
            )
            audio = demod.process(np.exp(1j * phase))
            audio = audio[len(audio) // 4:]  # drop filter startup transient
            return np.max(np.abs(np.fft.rfft(audio * np.hanning(len(audio)))))

        # A tone at 1000 Hz is in band; the same-deviation tone at 6000 Hz is
        # above the output Nyquist and would fold down to 2000 Hz if passed.
        in_band = peak_amplitude(1000.0)
        above_nyquist = peak_amplitude(6000.0)
        rejection_db = 20 * np.log10(above_nyquist / in_band)
        self.assertLess(rejection_db, -60.0)

        # Just above the cutoff is the hardest case, and was the worst one
        # before: 4500 Hz folds to 3500 Hz, right in the middle of speech.
        just_above = peak_amplitude(4500.0)
        self.assertLess(20 * np.log10(just_above / in_band), -60.0)

    def test_nfm_audio_decimation_stays_a_single_stage(self):
        # nfm is validated against real hardware; the cascade must not change
        # its filtering at all.
        demod = MODULE.FmStreamingDemodulator(16000, 128000.0, 4, 4, 5000.0)
        self.assertEqual(demod.audio_decimator.decimations, [4])


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

        demod = MODULE.FmStreamingDemodulator(16000, raw_iq_rate_hz, stage1, stage2, deviation_hz)
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

        reference = MODULE.FmStreamingDemodulator(16000, raw_iq_rate_hz, stage1, stage2, deviation_hz).process(iq)

        chunked = MODULE.FmStreamingDemodulator(16000, raw_iq_rate_hz, stage1, stage2, deviation_hz)
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
        # Relative tolerance: output is now normalized to ~+/-1.0 regardless
        # of deviation_hz, so an absolute threshold sized for the old raw-Hz
        # scale would be far looser than intended.
        self.assertLess(max_difference, 1e-9 * np.max(np.abs(reference[skip:n])))


class FmSquelchTests(unittest.TestCase):
    def test_signal_present_passes_audio(self):
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 4, 4
        t = np.arange(int(raw_iq_rate_hz * 0.1)) / raw_iq_rate_hz
        iq = np.exp(1j * 2 * np.pi * 1000 * t)  # strong, full-amplitude signal

        demod = MODULE.FmStreamingDemodulator(
            16000, raw_iq_rate_hz, stage1, stage2, 5000.0, squelch_db=-20, squelch_hang_ms=50,
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
            16000, raw_iq_rate_hz, stage1, stage2, 5000.0, squelch_db=-20, squelch_hang_ms=50,
        )
        audio = demod.process(iq)
        np.testing.assert_array_equal(audio, np.zeros_like(audio))

    def test_hang_time_keeps_squelch_open_until_hang_budget_expires(self):
        # Use true (exact-zero) silence chunks, fed as several successive
        # process() calls, so the channel filter's FIR tail from the
        # preceding strong signal fully decays within the first chunk or
        # two -- confirmed empirically: with squelch_hang_ms effectively
        # disabled (0.001ms -> 0 hang samples), squelch closes after just
        # one 5ms silence chunk, proving the *measured* power alone (not
        # any lingering filter transient) already reads below squelch_db
        # from then on. So if hang time is working, staying open across
        # many more such chunks can only be due to the hang mechanism.
        # With squelch_hang_ms=100 (hang budget = 3200 intermediate-rate
        # samples), squelch should stay open through 75ms of true silence
        # and only close once 125ms of true silence has elapsed, exceeding
        # the 100ms budget.
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 4, 4
        n_signal = int(raw_iq_rate_hz * 0.02)
        t_signal = np.arange(n_signal) / raw_iq_rate_hz
        signal = np.exp(1j * 2 * np.pi * 1000 * t_signal)
        silence_chunk = np.zeros(int(raw_iq_rate_hz * 0.005), dtype=np.complex128)  # 5ms

        demod = MODULE.FmStreamingDemodulator(
            16000, raw_iq_rate_hz, stage1, stage2, 5000.0, squelch_db=-20, squelch_hang_ms=100,
        )
        demod.process(signal)

        for _ in range(15):  # 75ms of true silence: well within the 100ms hang budget
            demod.process(silence_chunk)
        self.assertTrue(
            demod.squelch_open,
            "squelch closed before the hang budget was exhausted",
        )

        for _ in range(10):  # 50ms more: 125ms total, past the 100ms hang budget
            demod.process(silence_chunk)
        self.assertFalse(
            demod.squelch_open,
            "squelch never closed once the hang budget ran out",
        )

    def test_long_hang_keeps_squelch_open_longer_than_short_hang(self):
        # Directly compare two demodulators fed the identical signal-then-
        # true-silence sequence, differing only in squelch_hang_ms. If hang
        # time were broken or absent, both instances would close at the
        # same instant, since they'd be reacting to the same measured
        # power. The real mechanism keeps the long-hang instance open long
        # after the short-hang instance (hang_samples=0) has already
        # closed -- verified empirically: short_hang closes after the first
        # 5ms silence chunk, long_hang stays open through at least 19 more.
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 4, 4
        n_signal = int(raw_iq_rate_hz * 0.02)
        t_signal = np.arange(n_signal) / raw_iq_rate_hz
        signal = np.exp(1j * 2 * np.pi * 1000 * t_signal)
        silence_chunk = np.zeros(int(raw_iq_rate_hz * 0.005), dtype=np.complex128)  # 5ms

        long_hang = MODULE.FmStreamingDemodulator(
            16000, raw_iq_rate_hz, stage1, stage2, 5000.0, squelch_db=-20, squelch_hang_ms=100,
        )
        short_hang = MODULE.FmStreamingDemodulator(
            16000, raw_iq_rate_hz, stage1, stage2, 5000.0, squelch_db=-20, squelch_hang_ms=0.001,
        )
        long_hang.process(signal)
        short_hang.process(signal)

        for _ in range(5):  # 25ms of true silence
            long_hang.process(silence_chunk)
            short_hang.process(silence_chunk)

        self.assertTrue(long_hang.squelch_open)
        self.assertFalse(short_hang.squelch_open)


class FmDeemphasisTests(unittest.TestCase):
    def test_deemphasis_attenuates_high_frequency_relative_to_low(self):
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 4, 4
        low_freq_hz = 300.0
        high_freq_hz = 2500.0
        deviation_hz = 3000.0
        duration = 0.3
        t = np.arange(int(raw_iq_rate_hz * duration)) / raw_iq_rate_hz

        def make_iq(audio_freq_hz):
            phase = -(deviation_hz / audio_freq_hz) * np.cos(2 * np.pi * audio_freq_hz * t)
            return np.exp(1j * phase)

        def peak_amplitude(audio_freq_hz, deemphasis_us):
            demod = MODULE.FmStreamingDemodulator(
                16000, raw_iq_rate_hz, stage1, stage2, deviation_hz, deemphasis_us=deemphasis_us,
            )
            audio = demod.process(make_iq(audio_freq_hz))
            audio = audio[len(audio) // 4:]  # drop filter startup transient
            spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
            return np.max(spectrum)

        low_flat = peak_amplitude(low_freq_hz, None)
        high_flat = peak_amplitude(high_freq_hz, None)
        low_deemphasized = peak_amplitude(low_freq_hz, 6000.0)
        high_deemphasized = peak_amplitude(high_freq_hz, 6000.0)

        ratio_flat = high_flat / low_flat
        ratio_deemphasized = high_deemphasized / low_deemphasized
        self.assertLess(ratio_deemphasized, ratio_flat)


class AmStreamingDemodulatorTests(unittest.TestCase):
    def test_rejects_an_adjacent_channel_interferer_above_the_output_nyquist(self):
        # Regression test for the original single-decimation AM path: it went
        # straight from the raw IQ rate to the 8kHz output (a +/-4kHz complex
        # Nyquist) with only the wide channel-select filter in front, so
        # everything the 25kHz channel passed between 4kHz and 12.5kHz folded
        # directly into the audio band. A neighbouring airband transmission
        # 8.33kHz off frequency -- the standard VHF channel spacing the 25000
        # Hz default is documented to cover -- landed at 330 Hz only 4.7dB
        # below the wanted tone. With the intermediate rate and its audio
        # anti-alias filter it measures about -91dB; 50dB is a loose floor.
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = MODULE.choose_fm_decimation(raw_iq_rate_hz, 0.0, 25000.0)
        output_rate_hz = raw_iq_rate_hz / (stage1 * stage2)
        interferer_offset_hz = 8330.0
        duration = 0.5
        t = np.arange(int(raw_iq_rate_hz * duration)) / raw_iq_rate_hz

        wanted = (1.0 + 0.5 * np.cos(2 * np.pi * 1000 * t)).astype(np.complex128)
        interferer = 0.3 * np.exp(1j * 2 * np.pi * interferer_offset_hz * t)

        demod = MODULE.AmStreamingDemodulator(25000, raw_iq_rate_hz, stage1, stage2)
        audio = demod.process(wanted + interferer)
        audio = audio[len(audio) // 4:]  # drop filter/DC-block startup transient

        spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
        freqs = np.fft.rfftfreq(len(audio), d=1.0 / output_rate_hz)

        def peak_near(freq_hz):
            return spectrum[(freqs > freq_hz - 60) & (freqs < freq_hz + 60)].max()

        # 8330 Hz folds to 8330 - 8000 = 330 Hz at the 8kHz output rate.
        alias_hz = abs(interferer_offset_hz - round(interferer_offset_hz / output_rate_hz) * output_rate_hz)
        self.assertAlmostEqual(alias_hz, 330.0, delta=1.0)
        rejection_db = 20 * np.log10(peak_near(alias_hz) / peak_near(1000.0))
        self.assertLess(rejection_db, -50.0)

    def test_recovers_audio_tone_frequency_and_removes_dc_bias(self):
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 2, 8
        audio_freq_hz = 1000.0
        duration = 0.5
        t = np.arange(int(raw_iq_rate_hz * duration)) / raw_iq_rate_hz
        # A pure AM carrier tuned exactly to center frequency is a real,
        # positive-valued envelope at baseband -- no phase term needed.
        amplitude = 1.0 + 0.5 * np.cos(2 * np.pi * audio_freq_hz * t)
        iq = amplitude.astype(np.complex128)

        demod = MODULE.AmStreamingDemodulator(25000, raw_iq_rate_hz, stage1, stage2)
        audio = demod.process(iq)
        audio = audio[len(audio) // 4:]  # drop filter/DC-block startup transient

        self.assertLess(abs(np.mean(audio)), 0.05)  # carrier DC bias removed

        output_rate_hz = raw_iq_rate_hz / (stage1 * stage2)
        spectrum = np.fft.rfft(audio * np.hanning(len(audio)))
        freqs = np.fft.rfftfreq(len(audio), d=1.0 / output_rate_hz)
        peak_freq = freqs[np.argmax(np.abs(spectrum))]
        self.assertAlmostEqual(peak_freq, audio_freq_hz, delta=30.0)

    def test_streaming_chunks_match_single_batch_call(self):
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 2, 8
        audio_freq_hz = 1000.0
        duration = 0.3
        t = np.arange(int(raw_iq_rate_hz * duration)) / raw_iq_rate_hz
        amplitude = 1.0 + 0.5 * np.cos(2 * np.pi * audio_freq_hz * t)
        iq = amplitude.astype(np.complex128)

        reference = MODULE.AmStreamingDemodulator(25000, raw_iq_rate_hz, stage1, stage2).process(iq)

        chunked = MODULE.AmStreamingDemodulator(25000, raw_iq_rate_hz, stage1, stage2)
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
        skip = 20  # filter/DC-block startup transient
        max_difference = np.max(np.abs(reference[skip:n] - streamed[skip:n]))
        self.assertLess(max_difference, 1e-6)


class AmSquelchTests(unittest.TestCase):
    def test_signal_present_passes_audio(self):
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 2, 8
        t = np.arange(8000) / raw_iq_rate_hz
        amplitude = 1.0 + 0.5 * np.cos(2 * np.pi * 1000 * t)
        iq = amplitude.astype(np.complex128)

        demod = MODULE.AmStreamingDemodulator(
            25000, raw_iq_rate_hz, stage1, stage2, squelch_db=-20, squelch_hang_ms=50,
        )
        audio = demod.process(iq)
        self.assertGreater(np.max(np.abs(audio[len(audio) // 4:])), 0.0)

    def test_silence_below_threshold_is_squelched(self):
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 2, 8
        rng = np.random.default_rng(1)
        n = 8000
        iq = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 1e-4
        demod = MODULE.AmStreamingDemodulator(
            25000, raw_iq_rate_hz, stage1, stage2, squelch_db=-20, squelch_hang_ms=50,
        )
        audio = demod.process(iq)
        np.testing.assert_array_equal(audio, np.zeros_like(audio))

    def test_long_hang_keeps_squelch_open_longer_than_short_hang(self):
        # Same comparative technique used in FmSquelchTests: feed identical
        # signal-then-true-silence sequences to two demodulators differing
        # only in squelch_hang_ms, so a broken/absent hang mechanism would
        # make both close at the same instant (proven empirically: with
        # short_hang's budget effectively zero, it closes after the first
        # true-silence chunk).
        raw_iq_rate_hz = 128000.0
        stage1, stage2 = 2, 8
        t_signal = np.arange(int(raw_iq_rate_hz * 0.02)) / raw_iq_rate_hz  # 20ms
        signal = (1.0 + 0.5 * np.cos(2 * np.pi * 1000 * t_signal)).astype(np.complex128)
        silence_chunk = np.zeros(int(raw_iq_rate_hz * 0.005), dtype=np.complex128)  # 5ms

        long_hang = MODULE.AmStreamingDemodulator(
            25000, raw_iq_rate_hz, stage1, stage2, squelch_db=-20, squelch_hang_ms=100,
        )
        short_hang = MODULE.AmStreamingDemodulator(
            25000, raw_iq_rate_hz, stage1, stage2, squelch_db=-20, squelch_hang_ms=0.001,
        )
        long_hang.process(signal)
        short_hang.process(signal)

        for _ in range(5):  # 25ms of true silence
            long_hang.process(silence_chunk)
            short_hang.process(silence_chunk)

        self.assertTrue(long_hang.squelch_open)
        self.assertFalse(short_hang.squelch_open)


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


class BuildDemodulatorTests(unittest.TestCase):
    def test_builds_fm_demodulator_for_nfm_mode(self):
        config = {
            "mode": "nfm", "deviation_hz": 5000.0, "channel_bandwidth_hz": 16000.0,
            "squelch_db": None, "squelch_hang_ms": 200.0, "deemphasis_us": None,
        }
        demod = MODULE.build_demodulator(config, object(), 128000.0, 8000, {"lsb", "usb"})
        self.assertIsInstance(demod, MODULE.FmStreamingDemodulator)

    def test_builds_ssb_demodulator_for_lsb_mode(self):
        config = {"mode": "lsb", "low_cut_hz": -2700.0, "high_cut_hz": -300.0}
        demod = MODULE.build_demodulator(config, object(), 128000.0, 8000, {"lsb", "usb"})
        self.assertIsInstance(demod, MODULE.StreamingDemodulator)

    def test_builds_am_demodulator_for_am_mode(self):
        config = {
            "mode": "am", "channel_bandwidth_hz": 25000.0,
            "squelch_db": None, "squelch_hang_ms": 200.0,
        }
        demod = MODULE.build_demodulator(config, object(), 128000.0, 8000, {"lsb", "usb"})
        self.assertIsInstance(demod, MODULE.AmStreamingDemodulator)

    def test_am_mode_keeps_an_intermediate_rate_wider_than_the_channel(self):
        # AM sizes stage 1 through choose_fm_decimation with deviation_hz=0,
        # which reduces it to the channel-bandwidth constraint. The point is
        # that envelope detection happens at a rate that still holds the whole
        # channel, so adjacent-channel energy is filtered out rather than
        # folded into the audio band by the decimation.
        config = {
            "mode": "am", "channel_bandwidth_hz": 25000.0,
            "squelch_db": None, "squelch_hang_ms": 200.0,
        }
        demod = MODULE.build_demodulator(config, object(), 128000.0, 8000, {"lsb", "usb"})
        self.assertEqual((demod.stage1_decimation, demod.stage2_decimation), (2, 8))
        self.assertEqual(demod.intermediate_rate_hz, 64000.0)
        self.assertGreater(demod.intermediate_rate_hz, config["channel_bandwidth_hz"])
        self.assertEqual(demod.audio_decimator.output_rate_hz, 8000.0)

    def test_am_mode_tolerates_real_hardware_clock_imprecision(self):
        config = {
            "mode": "am", "channel_bandwidth_hz": 25000.0,
            "squelch_db": None, "squelch_hang_ms": 200.0,
        }
        demod = MODULE.build_demodulator(config, object(), 127999.0, 8000, {"lsb", "usb"})
        self.assertIsInstance(demod, MODULE.AmStreamingDemodulator)

    def test_am_mode_rejects_a_rate_nowhere_near_a_multiple(self):
        config = {
            "mode": "am", "channel_bandwidth_hz": 25000.0,
            "squelch_db": None, "squelch_hang_ms": 200.0,
        }
        with self.assertRaises(ValueError):
            MODULE.build_demodulator(config, object(), 128500.0, 8000, {"lsb", "usb"})

    def test_raises_for_unhandled_mode(self):
        config = {"mode": "bogus"}
        with self.assertRaises(AssertionError):
            MODULE.build_demodulator(config, object(), 128000.0, 8000, {"lsb", "usb"})

    def test_builds_fm_demodulator_for_wfm_mode(self):
        config = {
            "mode": "wfm", "deviation_hz": 75000.0, "channel_bandwidth_hz": 200000.0,
            "squelch_db": None, "squelch_hang_ms": 200.0, "deemphasis_us": 50.0,
        }
        demod = MODULE.build_demodulator(config, object(), 512000.0, 8000, {"lsb", "usb"})
        self.assertIsInstance(demod, MODULE.FmStreamingDemodulator)

    def test_wfm_mode_uses_backend_wfm_numtaps_when_present(self):
        class FakeBackend:
            NUMTAPS = 257
            WFM_NUMTAPS = 129
        config = {
            "mode": "wfm", "deviation_hz": 75000.0, "channel_bandwidth_hz": 200000.0,
            "squelch_db": None, "squelch_hang_ms": 200.0, "deemphasis_us": 50.0,
        }
        demod = MODULE.build_demodulator(config, FakeBackend(), 512000.0, 8000, {"lsb", "usb"})
        self.assertEqual(len(demod.channel_taps), 129)

    def test_wfm_mode_falls_back_to_default_numtaps_without_backend_override(self):
        config = {
            "mode": "wfm", "deviation_hz": 75000.0, "channel_bandwidth_hz": 200000.0,
            "squelch_db": None, "squelch_hang_ms": 200.0, "deemphasis_us": 50.0,
        }
        demod = MODULE.build_demodulator(config, object(), 512000.0, 8000, {"lsb", "usb"})
        self.assertEqual(len(demod.channel_taps), MODULE.DEFAULT_NUMTAPS)

    def test_uses_backend_numtaps_when_present(self):
        class FakeBackend:
            NUMTAPS = 99
        config = {"mode": "lsb", "low_cut_hz": -2700.0, "high_cut_hz": -300.0}
        demod = MODULE.build_demodulator(config, FakeBackend(), 128000.0, 8000, {"lsb", "usb"})
        self.assertEqual(len(demod.taps), 99)

    def test_ssb_tolerates_real_hardware_clock_imprecision(self):
        # Real hardware asked for 128000 Hz commonly reports back a value
        # like 127999 Hz (PLL/crystal clock quantization), not a
        # misconfiguration - this must still build a demodulator rather
        # than raise.
        config = {"mode": "lsb", "low_cut_hz": -2700.0, "high_cut_hz": -300.0}
        demod = MODULE.build_demodulator(config, object(), 127999.0, 8000, {"lsb", "usb"})
        self.assertIsInstance(demod, MODULE.StreamingDemodulator)

    def test_ssb_still_rejects_a_rate_nowhere_near_a_multiple(self):
        config = {"mode": "lsb", "low_cut_hz": -2700.0, "high_cut_hz": -300.0}
        with self.assertRaises(ValueError):
            MODULE.build_demodulator(config, object(), 128500.0, 8000, {"lsb", "usb"})


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
