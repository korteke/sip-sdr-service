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

GAIN_MODE_CHOICES = {"agc", "manual"}


def load_backend_config():
    gain_mode = env_choice("PLUTOSDR_GAIN_MODE", "agc", GAIN_MODE_CHOICES)
    gain_db = env_float("PLUTOSDR_GAIN_DB", 30)
    return {
        "gain_mode": gain_mode,
        "gain_db": gain_db,
    }


def open_device(frequency_hz, backend_config):
    import SoapySDR
    from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32

    device = SoapySDR.Device({"driver": DRIVER_KEY})
    device.setSampleRate(SOAPY_SDR_RX, 0, DEVICE_SAMPLE_RATE_HZ)
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
