#!/usr/bin/env python3
"""SDRplay RSP2pro backend adapter."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from sdr_env import env_choice, env_float

DRIVER_KEY = "sdrplay"

DEVICE_SAMPLE_RATE_HZ = 2048000.0
HW_DECIMATION = 16
IQ_SAMPLE_RATE_HZ = DEVICE_SAMPLE_RATE_HZ / HW_DECIMATION  # 128000.0
NUMTAPS = 257  # sized for the 128000 Hz effective IQ rate above

ANTENNA_CHOICES = {"a", "b", "hiz"}
GAIN_MODE_CHOICES = {"agc", "manual"}
ANTENNA_NAMES = {"a": "Antenna A", "b": "Antenna B", "hiz": "Hi-Z"}


def load_backend_config():
    antenna = env_choice("SDRPLAY_ANTENNA", "a", ANTENNA_CHOICES)
    gain_mode = env_choice("SDRPLAY_GAIN_MODE", "agc", GAIN_MODE_CHOICES)
    gain_reduction_db = env_float("SDRPLAY_GAIN_REDUCTION_DB", 40)
    lna_state = env_float("SDRPLAY_LNA_STATE", 0)
    bias_t = os.environ.get("SDRPLAY_BIAS_T", "off").lower() == "on"
    return {
        "antenna": antenna,
        "gain_mode": gain_mode,
        "gain_reduction_db": gain_reduction_db,
        "lna_state": lna_state,
        "bias_t": bias_t,
    }


def open_device(frequency_hz, backend_config):
    import SoapySDR
    from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32

    device = SoapySDR.Device({"driver": DRIVER_KEY})
    device.setSampleRate(SOAPY_SDR_RX, 0, DEVICE_SAMPLE_RATE_HZ)
    device.setFrequency(SOAPY_SDR_RX, 0, frequency_hz)

    try:
        device.writeSetting("decimation", str(HW_DECIMATION))
    except RuntimeError as error:
        print(f"SDR_DECIMATION_SETTING_FAILED error={error}", file=sys.stderr, flush=True)

    antenna_name = ANTENNA_NAMES[backend_config["antenna"]]
    available_antennas = device.listAntennas(SOAPY_SDR_RX, 0)
    if antenna_name in available_antennas:
        device.setAntenna(SOAPY_SDR_RX, 0, antenna_name)
    else:
        print(
            f"SDR_ANTENNA_NOT_FOUND requested={antenna_name} available={available_antennas}",
            file=sys.stderr, flush=True,
        )

    if backend_config["gain_mode"] == "agc":
        device.setGainMode(SOAPY_SDR_RX, 0, True)
    else:
        device.setGainMode(SOAPY_SDR_RX, 0, False)
        available_gains = device.listGains(SOAPY_SDR_RX, 0)
        if "IFGR" in available_gains:
            device.setGain(SOAPY_SDR_RX, 0, "IFGR", backend_config["gain_reduction_db"])
        else:
            print(
                f"SDR_GAIN_ELEMENT_NOT_FOUND requested=IFGR available={available_gains}",
                file=sys.stderr, flush=True,
            )
        if "RFGR" in available_gains:
            device.setGain(SOAPY_SDR_RX, 0, "RFGR", backend_config["lna_state"])
        else:
            print(
                f"SDR_GAIN_ELEMENT_NOT_FOUND requested=RFGR available={available_gains}",
                file=sys.stderr, flush=True,
            )

    if backend_config["bias_t"]:
        try:
            device.writeSetting("biasT_ctrl", "true")
        except RuntimeError as error:
            print(f"SDR_BIAS_T_SETTING_FAILED error={error}", file=sys.stderr, flush=True)

    stream = device.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
    device.activateStream(stream)
    actual_iq_sample_rate_hz = device.getSampleRate(SOAPY_SDR_RX, 0) / HW_DECIMATION
    return device, stream, actual_iq_sample_rate_hz
