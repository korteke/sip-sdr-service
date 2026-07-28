#!/usr/bin/env python3
"""Open the configured SDR backend and require at least one PCM frame."""

import os
import sys
import time

import numpy as np

sys.path.insert(0, "/opt/sip-sdr-service")

from sdr_stream import FRAME_BYTES, close_device, load_config
import sdr_demod

READ_CHUNK_SAMPLES = 4096

timeout_seconds = float(os.environ.get("SDR_TEST_TIMEOUT_SECONDS", "20"))
config = load_config()
frequency_hz = config["frequency_khz"] * 1000.0
device, stream, iq_sample_rate_hz, decimation = config["backend"].open_device(
    frequency_hz, config["backend_config"],
)
try:
    if iq_sample_rate_hz / decimation != 8000:
        raise ValueError(
            f"iq_sample_rate_hz={iq_sample_rate_hz:g} / decimation={decimation} "
            f"= {iq_sample_rate_hz / decimation:g} Hz, expected 8000 Hz"
        )
    demodulator = sdr_demod.StreamingDemodulator(
        config["low_cut_hz"], config["high_cut_hz"], iq_sample_rate_hz, decimation,
        numtaps=getattr(config["backend"], "NUMTAPS", sdr_demod.DEFAULT_NUMTAPS),
    )
except Exception:
    close_device(device, stream)
    raise
deadline = time.monotonic() + timeout_seconds
received = bytearray()
read_buffer = np.zeros(READ_CHUNK_SAMPLES, dtype=np.complex64)

try:
    while time.monotonic() < deadline:
        result = device.readStream(stream, [read_buffer], READ_CHUNK_SAMPLES, timeoutUs=200000)
        if result.ret > 0:
            audio = demodulator.process(read_buffer[:result.ret].astype(np.complex128))
            received.extend(sdr_demod.audio_to_pcm16(audio, float(os.environ.get("SDR_AUDIO_GAIN", "20.0"))))
            if len(received) >= FRAME_BYTES:
                print(f"RECEIVER_TEST_OK bytes={len(received)}", file=sys.stderr)
                raise SystemExit(0)
    print(f"RECEIVER_TEST_FAILED bytes={len(received)} timeout_seconds={timeout_seconds:g}", file=sys.stderr)
    raise SystemExit(1)
finally:
    close_device(device, stream)
