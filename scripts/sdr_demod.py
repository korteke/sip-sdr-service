#!/usr/bin/env python3
"""Streaming single-sideband demodulator for SDR IQ samples."""

import numpy as np
from scipy.signal import firwin, lfilter

SIDEBAND_CROSSOVER_KHZ = 10000.0
DEFAULT_NUMTAPS = 257
FM_INTERMEDIATE_RATE_MARGIN = 1.5  # headroom above Carson's-rule bandwidth, for filter roll-off


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
    exactly target_rate_hz. Raises ValueError if no such integer split
    exists for the given backend rate / deviation / bandwidth combination.
    """
    if raw_iq_rate_hz % target_rate_hz != 0:
        raise ValueError(
            f"raw_iq_rate_hz={raw_iq_rate_hz:g} is not an integer multiple of "
            f"target_rate_hz={target_rate_hz:g}"
        )
    total_decimation = int(round(raw_iq_rate_hz / target_rate_hz))
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


def audio_to_pcm16(audio, gain=1.0):
    """Scale float audio samples to little-endian int16 PCM bytes, clipping
    to full scale.
    """
    scaled = np.clip(audio * gain * 32767.0, -32768, 32767)
    return scaled.astype("<i2").tobytes()
