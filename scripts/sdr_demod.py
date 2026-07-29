#!/usr/bin/env python3
"""Streaming single-sideband demodulator for SDR IQ samples."""

import numpy as np
from scipy.signal import firwin, lfilter

SIDEBAND_CROSSOVER_KHZ = 10000.0
DEFAULT_NUMTAPS = 257
FM_INTERMEDIATE_RATE_MARGIN = 1.5  # headroom above Carson's-rule bandwidth, for filter roll-off
AUDIO_CUTOFF_MARGIN = 0.9  # headroom below stage-2 Nyquist, for filter roll-off
# Largest decimation ratio allowed in one audio-side lowpass-then-decimate
# sub-stage that is *not* the last one. A non-final sub-stage only has to keep
# its own fold-back out of the final audio band, which sits far below that
# sub-stage's own post-decimation Nyquist, so a gentle transition is harmless
# and a large ratio is the cheap way to shed rate.
AUDIO_MAX_SUBSTAGE_DECIMATION = 8
# The last sub-stage is the strict one: its stopband has to start essentially
# at the final output Nyquist, only 1/AUDIO_CUTOFF_MARGIN above its own
# cutoff. An FIR's transition width scales with its *input* rate for a fixed
# tap count, so the last sub-stage's input rate (output_rate * its ratio) has
# to stay low. 4 is the largest ratio the already-hardware-validated NFM audio
# filter realizes cleanly (257 taps at a 32000 Hz input rate); larger ratios
# need proportionally more taps than the wideband tap counts provide, which is
# exactly how a single 64x stage-2 ended up only ~4dB down at 4kHz.
AUDIO_MAX_FINAL_DECIMATION = 4
DEFAULT_SQUELCH_HANG_MS = 200
# Real hardware clocks (crystal oscillators, PLL dividers) are never
# perfectly exact - e.g. a PlutoSDR asked for 128000 Hz commonly reports
# 127999 Hz back (about 8 ppm off), so decimation math can't require a
# bit-exact multiple of the target rate. This tolerance is generous
# relative to typical hardware clock error while still easily catching a
# genuinely wrong/misconfigured rate (which would be off by far more).
RATE_TOLERANCE_RATIO = 1e-4
FM_MODES = {"nfm", "wfm"}


def matches_rate_multiple(rate_hz, target_rate_hz):
    """Return the nearest integer multiple of target_rate_hz if rate_hz is
    within RATE_TOLERANCE_RATIO of it, else None.
    """
    multiple = round(rate_hz / target_rate_hz)
    if multiple <= 0:
        return None
    if abs(rate_hz - multiple * target_rate_hz) > rate_hz * RATE_TOLERANCE_RATIO:
        return None
    return multiple


def resolve_mode(mode, frequency_khz):
    """Resolve SDR_MODE, expanding "auto" to lsb/usb by ham convention:
    below 10,000 kHz is LSB, at/above 10,000 kHz is USB. Explicit lsb/usb
    values pass through unchanged regardless of frequency.
    """
    mode = mode.lower()
    if mode != "auto":
        return mode
    return "lsb" if frequency_khz < SIDEBAND_CROSSOVER_KHZ else "usb"


def design_shifted_filter(low_cut_hz, high_cut_hz, sample_rate_hz, numtaps=DEFAULT_NUMTAPS):
    """Build a frequency-translating FIR filter that isolates the band
    [low_cut_hz, high_cut_hz] relative to the tuned carrier (0 Hz), without
    shifting the signal's own frequency. This is the standard technique for
    selecting a single sideband directly from complex baseband IQ: a real
    lowpass prototype sized to half the passband width is modulated by a
    complex exponential at the passband's center frequency, which moves the
    filter's passband without moving the signal.
    """
    if low_cut_hz >= high_cut_hz:
        raise ValueError("low_cut_hz must be lower than high_cut_hz")
    center_hz = (low_cut_hz + high_cut_hz) / 2.0
    half_bandwidth_hz = (high_cut_hz - low_cut_hz) / 2.0
    prototype = firwin(numtaps, half_bandwidth_hz, fs=sample_rate_hz, window=("kaiser", 8.0))
    sample_index = np.arange(numtaps)
    shift = np.exp(1j * 2 * np.pi * center_hz * sample_index / sample_rate_hz)
    return (prototype * shift).astype(np.complex128)


def design_lowpass_filter(bandwidth_hz, sample_rate_hz, numtaps=DEFAULT_NUMTAPS):
    """Build a real-valued lowpass FIR filter (stored as complex128 for a
    uniform lfilter interface with design_shifted_filter) isolating a
    channel of the given total bandwidth centered on the tuned carrier
    (0 Hz), with no frequency shift needed. This is the companion to
    design_shifted_filter for modes whose channel isn't offset from the
    carrier, e.g. NFM, as opposed to SSB's offset sideband.
    """
    if bandwidth_hz <= 0:
        raise ValueError("bandwidth_hz must be positive")
    half_bandwidth_hz = bandwidth_hz / 2.0
    taps = firwin(numtaps, half_bandwidth_hz, fs=sample_rate_hz, window=("kaiser", 8.0))
    return taps.astype(np.complex128)


def choose_fm_decimation(raw_iq_rate_hz, deviation_hz, channel_bandwidth_hz, target_rate_hz=8000):
    """Return (stage1_decimation, stage2_decimation) for two-stage FM
    demodulation. Stage 1 decimates raw complex IQ down to the smallest
    intermediate rate that both divides raw_iq_rate_hz evenly and stays
    above the Carson's-rule-derived bandwidth requirement (so the FM
    discriminator can resolve the configured deviation without aliasing).
    Stage 2 decimates the discriminator's real-valued audio output down to
    exactly target_rate_hz. Raises ValueError if raw_iq_rate_hz isn't within
    RATE_TOLERANCE_RATIO of an integer multiple of target_rate_hz.

    AM reuses this with deviation_hz=0: there is no discriminator and so no
    deviation-driven Nyquist floor, and the max() below then reduces the
    stage-1 sizing to the channel bandwidth alone, which is exactly the
    constraint the channel-select filter needs.
    """
    total_decimation = matches_rate_multiple(raw_iq_rate_hz, target_rate_hz)
    if total_decimation is None:
        raise ValueError(
            f"raw_iq_rate_hz={raw_iq_rate_hz:g} is not close enough to an integer multiple of "
            f"target_rate_hz={target_rate_hz:g}"
        )
    minimum_intermediate_rate_hz = FM_INTERMEDIATE_RATE_MARGIN * max(channel_bandwidth_hz, 2 * deviation_hz)

    best_stage1 = None
    for stage1 in range(1, total_decimation + 1):
        if total_decimation % stage1 != 0:
            continue
        intermediate_rate_hz = raw_iq_rate_hz / stage1
        if intermediate_rate_hz < minimum_intermediate_rate_hz:
            continue
        if best_stage1 is None or stage1 > best_stage1:
            best_stage1 = stage1

    if best_stage1 is None:
        raise ValueError(
            f"no integer decimation split of raw_iq_rate_hz={raw_iq_rate_hz:g} reaches "
            f"target_rate_hz={target_rate_hz:g} while keeping the intermediate rate above "
            f"{minimum_intermediate_rate_hz:g} Hz (channel_bandwidth_hz={channel_bandwidth_hz:g}, "
            f"deviation_hz={deviation_hz:g})"
        )
    return best_stage1, total_decimation // best_stage1


def _divisors_within(value, maximum):
    """Return the divisors of value in 2..maximum, ascending."""
    return [factor for factor in range(2, maximum + 1) if value % factor == 0]


def split_audio_decimation(total_decimation,
                           max_substage_decimation=AUDIO_MAX_SUBSTAGE_DECIMATION,
                           max_final_decimation=AUDIO_MAX_FINAL_DECIMATION):
    """Factor total_decimation into the per-sub-stage decimation ratios of a
    cascaded lowpass-then-decimate chain (product always equals
    total_decimation). Ratios small enough to filter cleanly in one shot are
    returned unsplit, so existing single-stage configurations keep their exact
    current behaviour. Otherwise the *last* ratio is made as small as possible
    (that sub-stage's lowpass is the one that has to be sharp right at the
    output Nyquist) and the earlier ratios absorb the rest, largest first.
    Falls back to a single unsplit stage when no usable factorization exists
    (e.g. a total decimation that is a large prime).
    """
    if total_decimation <= max_final_decimation:
        return [total_decimation]

    small_factors = _divisors_within(total_decimation, max_substage_decimation)
    if not small_factors:
        return [total_decimation]
    final_decimation = small_factors[0]

    leading = []
    remaining = total_decimation // final_decimation
    while remaining > max_substage_decimation:
        factors = _divisors_within(remaining, max_substage_decimation)
        if not factors:
            break
        leading.append(factors[-1])
        remaining //= factors[-1]
    if remaining > 1:
        leading.append(remaining)
    return leading + [final_decimation]


class CascadedAudioDecimator:
    """Reduces a real audio-rate stream by total_decimation through a cascade
    of lowpass-then-decimate sub-stages (see split_audio_decimation for how
    the ratio is split up). Each sub-stage gets its own anti-alias lowpass,
    cut AUDIO_CUTOFF_MARGIN below that sub-stage's own post-decimation
    Nyquist, so only the last one enforces the final audio bandwidth -- the
    earlier ones just have to avoid folding anything into it. Per-sub-stage
    FIR state and decimation phase are carried across process() calls, so
    chunk boundaries introduce no discontinuity regardless of chunk size,
    exactly like StreamingDemodulator does for a single stage.
    """

    def __init__(self, input_rate_hz, total_decimation, numtaps=DEFAULT_NUMTAPS):
        self.decimations = split_audio_decimation(total_decimation)
        self.taps = []
        self.filter_states = []
        self.phases = []
        self.cutoffs_hz = []

        rate_hz = input_rate_hz
        for decimation in self.decimations:
            cutoff_hz = AUDIO_CUTOFF_MARGIN * rate_hz / (2 * decimation)
            self.taps.append(firwin(numtaps, cutoff_hz, fs=rate_hz, window=("kaiser", 8.0)))
            self.filter_states.append(np.zeros(numtaps - 1, dtype=np.float64))
            self.phases.append(0)
            self.cutoffs_hz.append(cutoff_hz)
            rate_hz /= decimation
        self.output_rate_hz = rate_hz

    def process(self, samples):
        """Return a 1-D float64 array of decimated samples for the given real
        chunk (any length, including zero or one).
        """
        samples = np.asarray(samples, dtype=np.float64)
        for index, decimation in enumerate(self.decimations):
            # A short chunk can decimate down to nothing part-way through the
            # cascade; lfilter rejects an empty input, and there is no state
            # left to advance anyway.
            if len(samples) == 0:
                return np.zeros(0, dtype=np.float64)
            samples, self.filter_states[index] = lfilter(
                self.taps[index], [1.0], samples, zi=self.filter_states[index],
            )
            start = (-self.phases[index]) % decimation
            self.phases[index] = (self.phases[index] + len(samples)) % decimation
            samples = samples[start::decimation]
        return samples


class StreamingDemodulator:
    """Converts complex IQ chunks into real audio samples, carrying FIR
    filter state and decimation phase across calls so chunk boundaries
    introduce no discontinuity, regardless of chunk size.
    """

    def __init__(self, low_cut_hz, high_cut_hz, sample_rate_hz, decimation, numtaps=DEFAULT_NUMTAPS):
        self.taps = design_shifted_filter(low_cut_hz, high_cut_hz, sample_rate_hz, numtaps)
        self.filter_state = np.zeros(numtaps - 1, dtype=np.complex128)
        self.decimation = decimation
        self.phase = 0

    def process(self, iq_chunk):
        """Return a 1-D float64 array of decimated real audio samples for
        the given complex IQ chunk (any length, including zero or one).
        """
        iq_chunk = np.asarray(iq_chunk, dtype=np.complex128)
        filtered, self.filter_state = lfilter(self.taps, [1.0], iq_chunk, zi=self.filter_state)
        start = (-self.phase) % self.decimation
        decimated = filtered[start::self.decimation]
        self.phase = (self.phase + len(iq_chunk)) % self.decimation
        return decimated.real


class FmStreamingDemodulator:
    """Two-stage narrowband FM demodulator. A channel lowpass filter and
    partial ("stage 1") decimation bring the IQ signal to a rate wide
    enough to resolve the configured deviation without aliasing (see
    choose_fm_decimation). A phase discriminator then converts consecutive
    complex samples to instantaneous frequency, normalized by deviation_hz
    so a full-scale +/-deviation_hz swing maps to roughly +/-1.0 regardless
    of which deviation is configured -- matching the ~unity output scale
    SSB/AM demodulators already produce, so a single SDR_AUDIO_GAIN default
    can behave consistently across modes instead of needing to scale with
    deviation_hz itself. It carries its previous sample across process()
    calls the same way StreamingDemodulator
    carries filter state, so chunk boundaries introduce no discontinuity.
    An optional power-threshold squelch (with hang-time) then gates that
    discriminator output, muting audio when the channel-filtered IQ
    signal's power drops below squelch_db. An optional one-pole
    de-emphasis lowpass filter can then be applied to the (squelch-gated)
    discriminator output, when deemphasis_us is set -- for sources that
    pre-emphasize audio before transmission. Finally a CascadedAudioDecimator
    applies the anti-alias lowpass and the remaining ("stage 2") decimation
    to produce the final audio-rate output -- as a cascade rather than one
    step, because wideband FM's stage 2 is a 64x reduction that no single
    FIR of a sane length can anti-alias properly.
    """

    def __init__(self, channel_bandwidth_hz, raw_iq_rate_hz, stage1_decimation, stage2_decimation,
                 deviation_hz, squelch_db=None, squelch_hang_ms=DEFAULT_SQUELCH_HANG_MS, deemphasis_us=None,
                 numtaps=DEFAULT_NUMTAPS):
        self.channel_taps = design_lowpass_filter(channel_bandwidth_hz, raw_iq_rate_hz, numtaps)
        self.channel_filter_state = np.zeros(numtaps - 1, dtype=np.complex128)
        self.stage1_decimation = stage1_decimation
        self.stage1_phase = 0

        self.intermediate_rate_hz = raw_iq_rate_hz / stage1_decimation
        # Normalized so a full-scale +/-deviation_hz swing maps to roughly
        # +/-1.0, matching the ~unity output scale SSB/AM demodulators
        # already produce. Without this, a fixed SDR_AUDIO_GAIN can't work
        # across modes: raw (unnormalized) Hz-scale output is tens of
        # thousands for wfm's 75000 Hz deviation vs a few thousand for nfm's
        # 5000 Hz, so the same gain either clips wfm hard or leaves nfm
        # nearly silent. Confirmed empirically: real broadcast FM audio at
        # the un-normalized scale clipped 100% of samples at every gain
        # tried (2.0 through 20.0) during real-hardware bring-up.
        self.discriminator_scale = self.intermediate_rate_hz / (2 * np.pi * deviation_hz)
        # arbitrary unit-magnitude reference; the resulting first sample is startup transient, discarded by callers
        self.previous_sample = 1.0 + 0.0j

        self.stage2_decimation = stage2_decimation
        self.audio_decimator = CascadedAudioDecimator(
            self.intermediate_rate_hz, stage2_decimation, numtaps,
        )

        self.squelch_db = squelch_db
        self.squelch_open = squelch_db is None
        self.squelch_hang_samples = int(squelch_hang_ms / 1000.0 * self.intermediate_rate_hz)
        self.squelch_hang_remaining = 0

        # One-pole RC lowpass (backward-Euler discretization of tau*dy/dt + y = x):
        # y[n] = alpha*x[n] + (1-alpha)*y[n-1], alpha = T/(tau+T). Larger tau ->
        # smaller alpha -> more high-frequency rolloff, compensating for a
        # transmitter's pre-emphasis boost.
        if deemphasis_us is not None:
            tau_seconds = deemphasis_us * 1e-6
            sample_period_seconds = 1.0 / self.intermediate_rate_hz
            self.deemphasis_alpha = sample_period_seconds / (tau_seconds + sample_period_seconds)
            self.deemphasis_state = np.zeros(1, dtype=np.float64)
        else:
            self.deemphasis_alpha = None
            self.deemphasis_state = None

    def process(self, iq_chunk):
        """Return a 1-D float64 array of decimated real audio samples for
        the given complex IQ chunk (any length, including zero or one).
        """
        iq_chunk = np.asarray(iq_chunk, dtype=np.complex128)
        filtered, self.channel_filter_state = lfilter(
            self.channel_taps, [1.0], iq_chunk, zi=self.channel_filter_state,
        )

        start = (-self.stage1_phase) % self.stage1_decimation
        decimated = filtered[start::self.stage1_decimation]
        self.stage1_phase = (self.stage1_phase + len(iq_chunk)) % self.stage1_decimation

        if len(decimated) == 0:
            return np.zeros(0, dtype=np.float64)

        # Squelch state machine: open on strong signal, hold open during a
        # hang window after signal drops, then close.
        if self.squelch_db is not None:
            # Power is block-averaged over this call's samples, not a
            # continuously-smoothed estimate -- squelch responsiveness is
            # bounded by caller chunk size.
            # 1e-12 floor avoids log10(0) on exact silence.
            chunk_power_db = 10 * np.log10(max(np.mean(np.abs(decimated) ** 2), 1e-12))
            if chunk_power_db >= self.squelch_db:
                self.squelch_open = True
                self.squelch_hang_remaining = self.squelch_hang_samples
            elif self.squelch_hang_remaining > len(decimated):
                self.squelch_hang_remaining -= len(decimated)
            else:
                self.squelch_hang_remaining = 0
                self.squelch_open = False

        extended = np.concatenate(([self.previous_sample], decimated))
        instantaneous_frequency = (
            np.angle(extended[1:] * np.conj(extended[:-1])) * self.discriminator_scale
        )
        self.previous_sample = decimated[-1]

        if not self.squelch_open:
            instantaneous_frequency = np.zeros_like(instantaneous_frequency)

        if self.deemphasis_alpha is not None:
            instantaneous_frequency, self.deemphasis_state = lfilter(
                [self.deemphasis_alpha], [1.0, -(1.0 - self.deemphasis_alpha)],
                instantaneous_frequency, zi=self.deemphasis_state,
            )

        return self.audio_decimator.process(instantaneous_frequency)


AM_DC_BLOCK_CUTOFF_HZ = 20.0  # removes the carrier-strength bias from envelope detection


class AmStreamingDemodulator:
    """Two-stage AM demodulator, laid out to mirror
    FmStreamingDemodulator. A channel lowpass filter and partial
    ("stage 1") decimation bring the IQ signal to an intermediate rate
    that still holds the whole configured channel (see
    choose_fm_decimation with deviation_hz=0). An envelope detector (the
    complex magnitude of each decimated sample) then recovers the audio at
    that intermediate rate, plus a DC bias proportional to carrier
    strength -- the same relative position FM's phase discriminator
    occupies. An optional power-threshold squelch (with hang-time,
    identical state machine to FmStreamingDemodulator's) gates that
    envelope to zero when the channel-filtered signal's power drops below
    squelch_db. A fixed one-pole highpass (AM_DC_BLOCK_CUTOFF_HZ) then
    removes the carrier bias from the (possibly squelch-gated) envelope.
    Finally a CascadedAudioDecimator applies the audio anti-alias lowpass
    and the remaining ("stage 2") decimation to reach the output rate.

    That last filter is what keeps the intermediate rate worth having:
    decimating a full-channel-wide signal straight to 8kHz would fold
    everything the channel filter passes between 4kHz and half the channel
    bandwidth right into the audio band -- e.g. an adjacent airband
    transmission 8.33kHz off frequency landing on top of the wanted one.
    """

    def __init__(self, channel_bandwidth_hz, raw_iq_rate_hz, stage1_decimation, stage2_decimation,
                 squelch_db=None, squelch_hang_ms=DEFAULT_SQUELCH_HANG_MS,
                 numtaps=DEFAULT_NUMTAPS):
        self.channel_taps = design_lowpass_filter(channel_bandwidth_hz, raw_iq_rate_hz, numtaps)
        self.channel_filter_state = np.zeros(numtaps - 1, dtype=np.complex128)
        self.stage1_decimation = stage1_decimation
        self.stage1_phase = 0

        self.intermediate_rate_hz = raw_iq_rate_hz / stage1_decimation

        self.stage2_decimation = stage2_decimation
        self.audio_decimator = CascadedAudioDecimator(
            self.intermediate_rate_hz, stage2_decimation, numtaps,
        )

        # One-pole highpass (DC blocker): y[n] = x[n] - x[n-1] + pole*y[n-1].
        # pole = 1 - 2*pi*cutoff_hz/intermediate_rate_hz approximates the
        # desired -3dB cutoff for cutoff_hz << intermediate_rate_hz.
        dc_block_pole = 1.0 - (2 * np.pi * AM_DC_BLOCK_CUTOFF_HZ / self.intermediate_rate_hz)
        self.dc_block_b = [1.0, -1.0]
        self.dc_block_a = [1.0, -dc_block_pole]
        self.dc_block_state = np.zeros(1, dtype=np.float64)

        self.squelch_db = squelch_db
        self.squelch_open = squelch_db is None
        self.squelch_hang_samples = int(squelch_hang_ms / 1000.0 * self.intermediate_rate_hz)
        self.squelch_hang_remaining = 0

    def process(self, iq_chunk):
        """Return a 1-D float64 array of decimated real audio samples for
        the given complex IQ chunk (any length, including zero or one).
        """
        iq_chunk = np.asarray(iq_chunk, dtype=np.complex128)
        filtered, self.channel_filter_state = lfilter(
            self.channel_taps, [1.0], iq_chunk, zi=self.channel_filter_state,
        )

        start = (-self.stage1_phase) % self.stage1_decimation
        decimated = filtered[start::self.stage1_decimation]
        self.stage1_phase = (self.stage1_phase + len(iq_chunk)) % self.stage1_decimation

        if len(decimated) == 0:
            return np.zeros(0, dtype=np.float64)

        if self.squelch_db is not None:
            chunk_power_db = 10 * np.log10(max(np.mean(np.abs(decimated) ** 2), 1e-12))
            if chunk_power_db >= self.squelch_db:
                self.squelch_open = True
                self.squelch_hang_remaining = self.squelch_hang_samples
            elif self.squelch_hang_remaining > len(decimated):
                self.squelch_hang_remaining -= len(decimated)
            else:
                self.squelch_hang_remaining = 0
                self.squelch_open = False

        envelope = np.abs(decimated)
        if not self.squelch_open:
            envelope = np.zeros_like(envelope)

        blocked, self.dc_block_state = lfilter(
            self.dc_block_b, self.dc_block_a, envelope, zi=self.dc_block_state,
        )
        return self.audio_decimator.process(blocked)


def build_demodulator(config, backend, iq_sample_rate_hz, sample_rate_hz, ssb_modes):
    """Construct the right StreamingDemodulator/FmStreamingDemodulator for
    config["mode"], given an opened backend's intrinsic IQ rate. Shared by
    sdr_stream.py's main() and test_sdr.py so decimation/demodulator-
    selection logic has exactly one implementation.
    """
    if config["mode"] == "wfm":
        numtaps = getattr(backend, "WFM_NUMTAPS", DEFAULT_NUMTAPS)
    else:
        numtaps = getattr(backend, "NUMTAPS", DEFAULT_NUMTAPS)
    if config["mode"] in FM_MODES:
        stage1_decimation, stage2_decimation = choose_fm_decimation(
            iq_sample_rate_hz, config["deviation_hz"], config["channel_bandwidth_hz"],
            target_rate_hz=sample_rate_hz,
        )
        return FmStreamingDemodulator(
            config["channel_bandwidth_hz"], iq_sample_rate_hz,
            stage1_decimation, stage2_decimation, config["deviation_hz"],
            squelch_db=config["squelch_db"], squelch_hang_ms=config["squelch_hang_ms"],
            deemphasis_us=config["deemphasis_us"], numtaps=numtaps,
        )
    elif config["mode"] in ssb_modes:
        ssb_decimation = matches_rate_multiple(iq_sample_rate_hz, sample_rate_hz)
        if ssb_decimation is None:
            raise ValueError(
                f"iq_sample_rate_hz={iq_sample_rate_hz:g} is not close enough to an integer "
                f"multiple of {sample_rate_hz:g} Hz"
            )
        return StreamingDemodulator(
            config["low_cut_hz"], config["high_cut_hz"], iq_sample_rate_hz, ssb_decimation,
            numtaps=numtaps,
        )
    elif config["mode"] == "am":
        # deviation_hz=0: AM has no discriminator, so only the channel-select
        # filter's own bandwidth constrains the intermediate rate.
        stage1_decimation, stage2_decimation = choose_fm_decimation(
            iq_sample_rate_hz, 0.0, config["channel_bandwidth_hz"],
            target_rate_hz=sample_rate_hz,
        )
        return AmStreamingDemodulator(
            config["channel_bandwidth_hz"], iq_sample_rate_hz,
            stage1_decimation, stage2_decimation,
            squelch_db=config["squelch_db"], squelch_hang_ms=config["squelch_hang_ms"],
            numtaps=numtaps,
        )
    else:
        raise AssertionError(f"unhandled mode {config['mode']!r}: MODE_CHOICES/SSB_MODES may be out of sync")


def audio_to_pcm16(audio, gain=1.0):
    """Scale float audio samples to little-endian int16 PCM bytes, clipping
    to full scale.
    """
    scaled = np.clip(audio * gain * 32767.0, -32768, 32767)
    return scaled.astype("<i2").tobytes()
