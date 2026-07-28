#!/usr/bin/env python3
"""Produce paced 8 kHz signed-linear mono PCM from a local SDR backend."""

import importlib
import json
import os
import signal
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import sdr_demod
from sdr_env import env_choice, env_float

SAMPLE_RATE = 8000
SAMPLE_BYTES = 2
FRAME_SECONDS = 0.02
FRAME_BYTES = int(SAMPLE_RATE * SAMPLE_BYTES * FRAME_SECONDS)
SILENCE = bytes(FRAME_BYTES)
STATUS_PATH = Path("/run/sip-sdr/stream.json")
READ_CHUNK_SAMPLES = 4096

DRIVER_CHOICES = {"sdrplay", "rtlsdr", "plutosdr"}
MODE_CHOICES = {"lsb", "usb", "auto", "nfm"}
SSB_MODES = {"lsb", "usb"}


def load_config():
    driver = env_choice("SDR_DRIVER", "sdrplay", DRIVER_CHOICES)
    backend = importlib.import_module(f"sdr_backends.{driver}")

    frequency_khz = env_float("SDR_FREQUENCY_KHZ", 3699)
    if frequency_khz <= 0:
        raise ValueError("SDR_FREQUENCY_KHZ must be positive")

    mode_setting = env_choice("SDR_MODE", "lsb", MODE_CHOICES)
    mode = mode_setting if mode_setting == "nfm" else sdr_demod.resolve_mode(mode_setting, frequency_khz)

    backend_config = backend.load_backend_config()

    config = {
        "driver": driver,
        "backend": backend,
        "frequency_khz": frequency_khz,
        "mode_setting": mode_setting,
        "mode": mode,
        "backend_config": backend_config,
    }

    if mode in SSB_MODES:
        low_cut_hz = env_float("SDR_LOW_CUT_HZ", -2700)
        high_cut_hz = env_float("SDR_HIGH_CUT_HZ", -300)
        if abs(low_cut_hz) == abs(high_cut_hz):
            raise ValueError("SDR_LOW_CUT_HZ and SDR_HIGH_CUT_HZ must have different magnitudes")
        magnitude_low = min(abs(low_cut_hz), abs(high_cut_hz))
        magnitude_high = max(abs(low_cut_hz), abs(high_cut_hz))
        if mode == "lsb":
            config["low_cut_hz"], config["high_cut_hz"] = -magnitude_high, -magnitude_low
        else:
            config["low_cut_hz"], config["high_cut_hz"] = magnitude_low, magnitude_high
    else:
        deviation_hz = env_float("SDR_FM_DEVIATION_HZ", 5000)
        channel_bandwidth_hz = env_float("SDR_FM_CHANNEL_BANDWIDTH_HZ", 16000)
        if deviation_hz <= 0:
            raise ValueError("SDR_FM_DEVIATION_HZ must be positive")
        if channel_bandwidth_hz <= 0:
            raise ValueError("SDR_FM_CHANNEL_BANDWIDTH_HZ must be positive")

        squelch_setting = os.environ.get("SDR_SQUELCH_DB", "").strip()
        squelch_db = float(squelch_setting) if squelch_setting else None

        squelch_hang_ms = env_float("SDR_SQUELCH_HANG_MS", 200)
        if squelch_hang_ms <= 0:
            raise ValueError("SDR_SQUELCH_HANG_MS must be positive")

        deemphasis_setting = os.environ.get("SDR_FM_DEEMPHASIS_US", "").strip()
        deemphasis_us = float(deemphasis_setting) if deemphasis_setting else None
        if deemphasis_us is not None and deemphasis_us <= 0:
            raise ValueError("SDR_FM_DEEMPHASIS_US must be positive")

        config.update({
            "deviation_hz": deviation_hz,
            "channel_bandwidth_hz": channel_bandwidth_hz,
            "squelch_db": squelch_db,
            "squelch_hang_ms": squelch_hang_ms,
            "deemphasis_us": deemphasis_us,
        })

    return config


def milliseconds_to_bytes(milliseconds):
    return int(SAMPLE_RATE * SAMPLE_BYTES * milliseconds / 1000) & ~1


def resync_buffer(buffer, maximum_bytes, target_bytes):
    """Drop old PCM in one bounded resync instead of many tiny audible drops."""
    if len(buffer) <= maximum_bytes:
        return 0
    dropped = (len(buffer) - target_bytes) & ~1
    del buffer[:dropped]
    return dropped


def write_status(state, **details):
    payload = {"state": state, "updated_epoch": time.time(), **details}
    temporary = STATUS_PATH.with_suffix(".tmp")
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(STATUS_PATH)
    except OSError as error:
        print(f"SDR_STATUS_WRITE_FAILED error={error}", file=sys.stderr, flush=True)


def close_device(device, stream):
    if device is None:
        return
    if stream is not None:
        try:
            device.deactivateStream(stream)
            device.closeStream(stream)
        except RuntimeError as error:
            print(f"SDR_DEVICE_CLOSE_FAILED error={error}", file=sys.stderr, flush=True)


def main():
    import SoapySDR

    config = load_config()
    backend = config["backend"]
    frequency_hz = config["frequency_khz"] * 1000.0
    audio_gain = env_float("SDR_AUDIO_GAIN", 20.0)

    demodulator = None
    stopping = False
    device = None
    stream = None
    pcm_buffer = bytearray()
    last_audio = 0.0
    last_status = 0.0
    audio_active = False
    next_start = 0.0
    initial_backoff = env_float("SDR_RETRY_INITIAL_SECONDS", 2)
    backoff = initial_backoff
    maximum_backoff = env_float("SDR_RETRY_MAX_SECONDS", 30)
    stale_seconds = env_float("SDR_STALE_SECONDS", 15)
    target_buffer_bytes = milliseconds_to_bytes(env_float("SDR_BUFFER_TARGET_MS", 250))
    maximum_buffer_bytes = milliseconds_to_bytes(env_float("SDR_BUFFER_MAX_MS", 1000))

    if min(backoff, maximum_backoff, stale_seconds) <= 0:
        raise ValueError("retry and stale durations must be positive")
    if target_buffer_bytes < FRAME_BYTES or target_buffer_bytes >= maximum_buffer_bytes:
        raise ValueError("SDR_BUFFER_TARGET_MS must be at least 20 and lower than SDR_BUFFER_MAX_MS")

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    write_status("starting")
    deadline = time.monotonic()
    read_buffer = np.zeros(READ_CHUNK_SAMPLES, dtype=np.complex64)

    def disconnect(reason):
        nonlocal device, stream, audio_active, next_start, backoff
        print(f"SDR_DISCONNECTED reason={reason} retry_seconds={backoff:g}", file=sys.stderr, flush=True)
        write_status("disconnected", reason=reason, retry_seconds=backoff)
        close_device(device, stream)
        device = None
        stream = None
        audio_active = False
        pcm_buffer.clear()
        next_start = time.monotonic() + backoff
        backoff = min(backoff * 2, maximum_backoff)

    try:
        while not stopping:
            now = time.monotonic()
            if device is None and now >= next_start:
                try:
                    print(
                        f"SDR_CONNECT driver={config['driver']} frequency_khz={config['frequency_khz']:g} "
                        f"mode={config['mode']}",
                        file=sys.stderr, flush=True,
                    )
                    device, stream, iq_sample_rate_hz, decimation = backend.open_device(
                        frequency_hz, config["backend_config"],
                    )
                except Exception as error:
                    print(f"SDR_DEVICE_OPEN_FAILED error={error}", file=sys.stderr, flush=True)
                    device = None
                    stream = None
                    next_start = now + backoff
                    backoff = min(backoff * 2, maximum_backoff)

                if device is not None and demodulator is None:
                    # A failure here (bad rate, or an unbuildable filter design) is a
                    # configuration/hardware-compatibility problem, not a transient
                    # device error, so it must NOT be swallowed by the retry logic
                    # above: close the just-opened device and let it propagate all
                    # the way out to the __main__ guard's SDR_CONFIG_ERROR handler.
                    try:
                        if iq_sample_rate_hz / decimation != SAMPLE_RATE:
                            raise ValueError(
                                f"iq_sample_rate_hz={iq_sample_rate_hz:g} / decimation={decimation} "
                                f"= {iq_sample_rate_hz / decimation:g} Hz, expected {SAMPLE_RATE} Hz"
                            )
                        demodulator = sdr_demod.StreamingDemodulator(
                            config["low_cut_hz"], config["high_cut_hz"], iq_sample_rate_hz, decimation,
                            numtaps=getattr(backend, "NUMTAPS", sdr_demod.DEFAULT_NUMTAPS),
                        )
                    except Exception:
                        close_device(device, stream)
                        device = None
                        stream = None
                        raise

                if device is not None:
                    last_audio = now
                    audio_active = False
                    write_status("connecting")

            if device is not None:
                try:
                    result = device.readStream(
                        stream, [read_buffer], READ_CHUNK_SAMPLES, timeoutUs=int(FRAME_SECONDS * 1_000_000),
                    )
                except Exception as error:
                    print(f"SDR_READ_EXCEPTION error={error}", file=sys.stderr, flush=True)
                    disconnect("read_exception")
                    result = None

                if result is not None:
                    if result.ret > 0:
                        audio = demodulator.process(read_buffer[:result.ret].astype(np.complex128))
                        pcm_buffer.extend(sdr_demod.audio_to_pcm16(audio, audio_gain))
                        last_audio = time.monotonic()
                        backoff = initial_backoff
                        if not audio_active:
                            print("SDR_AUDIO_ACTIVE", file=sys.stderr, flush=True)
                            audio_active = True
                    elif result.ret == SoapySDR.SOAPY_SDR_OVERFLOW:
                        print("SDR_OVERFLOW", file=sys.stderr, flush=True)
                    elif result.ret == SoapySDR.SOAPY_SDR_TIMEOUT:
                        pass
                    elif result.ret < 0:
                        print(f"SDR_READ_ERROR code={result.ret}", file=sys.stderr, flush=True)
            else:
                time.sleep(max(0.0, min(FRAME_SECONDS, deadline - now)))

            now = time.monotonic()
            device_stale = device is not None and now - last_audio > stale_seconds
            if device_stale:
                disconnect("audio_stale")

            if now >= deadline:
                if len(pcm_buffer) >= FRAME_BYTES:
                    frame = bytes(pcm_buffer[:FRAME_BYTES])
                    del pcm_buffer[:FRAME_BYTES]
                else:
                    frame = SILENCE
                dropped = resync_buffer(pcm_buffer, maximum_buffer_bytes, target_buffer_bytes)
                if dropped:
                    print(
                        f"SDR_BUFFER_RESYNC dropped_bytes={dropped} target_bytes={target_buffer_bytes}",
                        file=sys.stderr, flush=True,
                    )
                try:
                    sys.stdout.buffer.write(frame)
                    sys.stdout.buffer.flush()
                except BrokenPipeError:
                    break
                deadline += FRAME_SECONDS
                if deadline < now - FRAME_SECONDS:
                    deadline = now + FRAME_SECONDS

            if audio_active and now - last_status >= 5:
                write_status("streaming", buffered_bytes=len(pcm_buffer))
                last_status = now
    finally:
        close_device(device, stream)
        write_status("stopped")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError) as error:
        print(f"SDR_CONFIG_ERROR {error}", file=sys.stderr, flush=True)
        raise SystemExit(64)
