#!/usr/bin/env python3
"""Streaming single-sideband demodulator for SDR IQ samples."""

import numpy as np
from scipy.signal import firwin, lfilter

SIDEBAND_CROSSOVER_KHZ = 10000.0
DEFAULT_NUMTAPS = 257


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
