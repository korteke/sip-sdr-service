#!/usr/bin/env python3
"""PlutoSDR (ADALM-PLUTO) backend adapter."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from sdr_env import env_choice, env_float

DRIVER_KEY = "plutosdr"

# The AD9361 RFIC supports arbitrary sample rates directly (down to roughly
# 65kHz), so no on-device decimation is needed. 128kHz matches SDRplay's
# effective rate so the demod filter design is reused without re-verifying
# it at a different input rate.
DEVICE_SAMPLE_RATE_HZ = 128000.0
NUMTAPS = 257  # sized for the 128000 Hz IQ rate above

# wfm needs a much wider raw rate to represent broadcast FM's +/-75kHz
# deviation without exceeding the complex-IQ Nyquist ceiling
# (sample_rate_hz/2). 512000 Hz clears the ~300kHz Carson's-rule floor
# for 75000 Hz deviation / 200000 Hz channel bandwidth with solid margin,
# and is a clean 64x multiple of the 8000 Hz output rate.
WFM_DEVICE_SAMPLE_RATE_HZ = 512000.0
# WFM's channel is far wider relative to its sample rate than NFM's is at
# 128000 Hz, so it needs fewer taps for equivalent selectivity despite the
# higher rate -- verified empirically in tests/test_sdr_demod.py's
# test_wfm_rate_needs_fewer_taps_for_equivalent_rejection.
WFM_NUMTAPS = 129

GAIN_MODE_CHOICES = {"agc", "manual"}


def sample_rate_for_mode(mode):
    return WFM_DEVICE_SAMPLE_RATE_HZ if mode == "wfm" else DEVICE_SAMPLE_RATE_HZ


def load_backend_config():
    gain_mode = env_choice("PLUTOSDR_GAIN_MODE", "agc", GAIN_MODE_CHOICES)
    gain_db = env_float("PLUTOSDR_GAIN_DB", 30)
    return {
        "gain_mode": gain_mode,
        "gain_db": gain_db,
    }


def open_device(frequency_hz, mode, backend_config):
    import SoapySDR
    from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32

    device = SoapySDR.Device({"driver": DRIVER_KEY})
    device.setSampleRate(SOAPY_SDR_RX, 0, sample_rate_for_mode(mode))
    device.setFrequency(SOAPY_SDR_RX, 0, frequency_hz)

    if backend_config["gain_mode"] == "agc":
        device.setGainMode(SOAPY_SDR_RX, 0, True)
    else:
        device.setGainMode(SOAPY_SDR_RX, 0, False)
        available_gains = device.listGains(SOAPY_SDR_RX, 0)
        if "PGA" in available_gains:
            device.setGain(SOAPY_SDR_RX, 0, "PGA", backend_config["gain_db"])
        else:
            print(
                f"SDR_GAIN_ELEMENT_NOT_FOUND requested=PGA available={available_gains}",
                file=sys.stderr, flush=True,
            )

    stream = device.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
    device.activateStream(stream)
    actual_iq_sample_rate_hz = device.getSampleRate(SOAPY_SDR_RX, 0)
    return device, stream, actual_iq_sample_rate_hz
