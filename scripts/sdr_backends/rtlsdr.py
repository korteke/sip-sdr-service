#!/usr/bin/env python3
"""RTL-SDR backend adapter."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from sdr_env import env_choice, env_float

DRIVER_KEY = "rtlsdr"

# RTL-SDR has no on-device decimation; request a directly-supported rate.
# 256kHz sits below the RTL-SDR's documented unreliable range of roughly
# 300-900kHz (verify against your specific tuner chip during bring-up).
DEVICE_SAMPLE_RATE_HZ = 256000.0
# 256000 Hz needs roughly twice the taps of the 128000 Hz backends to hold
# the same opposite-sideband rejection, since design_shifted_filter's
# transition width scales with sample rate for a fixed tap count. Measured
# with tests/test_sdr_demod.py: ~91dB rejection at 513 taps vs ~21dB at 257.
NUMTAPS = 513

GAIN_MODE_CHOICES = {"agc", "manual"}


def load_backend_config():
    gain_mode = env_choice("RTLSDR_GAIN_MODE", "agc", GAIN_MODE_CHOICES)
    gain_db = env_float("RTLSDR_GAIN_DB", 30)
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
        if "TUNER" in available_gains:
            device.setGain(SOAPY_SDR_RX, 0, "TUNER", backend_config["gain_db"])
        else:
            print(
                f"SDR_GAIN_ELEMENT_NOT_FOUND requested=TUNER available={available_gains}",
                file=sys.stderr, flush=True,
            )

    stream = device.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
    device.activateStream(stream)
    actual_iq_sample_rate_hz = device.getSampleRate(SOAPY_SDR_RX, 0)
    return device, stream, actual_iq_sample_rate_hz
