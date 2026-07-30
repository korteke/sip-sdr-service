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
TUNE_CONTROL_PATH = Path("/run/sip-sdr/tune-frequency")
CURRENT_FREQUENCY_PATH = Path("/run/sip-sdr/current-frequency-khz")
READ_CHUNK_SAMPLES = 4096

DRIVER_CHOICES = {"sdrplay", "rtlsdr", "plutosdr"}
MODE_CHOICES = {"lsb", "usb", "auto", "nfm", "wfm", "am"}
SSB_MODES = {"lsb", "usb"}


def default_audio_gain_for_mode(mode):
    """FM's demodulator output is normalized to roughly +/-1.0 at full
    deviation (see sdr_demod.FmStreamingDemodulator), while SSB/AM output
    is unnormalized raw IQ amplitude, typically well below 1.0 and needing
    a much larger gain to reach an audible level. One fixed SDR_AUDIO_GAIN
    default can't suit both scales.
    """
    return 1.0 if mode in sdr_demod.FM_MODES else 20.0


def _load_squelch_config():
    squelch_setting = os.environ.get("SDR_SQUELCH_DB", "").strip()
    squelch_db = float(squelch_setting) if squelch_setting else None

    squelch_hang_ms = env_float("SDR_SQUELCH_HANG_MS", 200)
    if squelch_hang_ms <= 0:
        raise ValueError("SDR_SQUELCH_HANG_MS must be positive")

    return squelch_db, squelch_hang_ms


def resolve_ssb_cuts(mode, low_cut_hz, high_cut_hz):
    """Apply the sign convention for the given resolved SSB mode (lsb/usb)
    to a pair of passband-edge magnitudes, regardless of what sign
    convention they currently carry -- idempotent, so it's safe to call
    again on a config's already-resolved cuts after a caller retunes across
    the 10MHz LSB/USB crossover (see resolve_mode).
    """
    magnitude_low = min(abs(low_cut_hz), abs(high_cut_hz))
    magnitude_high = max(abs(low_cut_hz), abs(high_cut_hz))
    if mode == "lsb":
        return -magnitude_high, -magnitude_low
    return magnitude_low, magnitude_high


TUNE_MIN_FREQUENCY_KHZ = 1800.0
TUNE_MAX_FREQUENCY_KHZ = 29999.0


def apply_tuned_frequency(config, iq_sample_rate_hz, sample_rate_hz, ssb_modes, frequency_khz, demodulator):
    """Apply a caller-entered frequency (already range-validated by the
    dialplan; TUNE_MIN/MAX_FREQUENCY_KHZ here is defense-in-depth against a
    stale or manually-edited control file) to config in place, rebuilding
    the demodulator only when the resolved LSB/USB sideband actually
    changes -- crossing sdr_demod.SIDEBAND_CROSSOVER_KHZ is the only case
    that needs a new filter; otherwise the caller keeps listening through
    the same demodulator instance with no interruption. Only valid for a
    config whose mode is already lsb/usb (caller tuning is SSB-only).

    sdr_demod.build_demodulator can itself raise (an unbuildable filter
    design, for example) -- config is only mutated in place *after* the
    new demodulator is built successfully, so a failed rebuild leaves
    config and the caller's still-running demodulator in a mutually
    consistent state (both describing the old, still-correct frequency)
    for the caller (main()'s retune block, via apply_retune_safely) to
    catch and log without corrupting the shared stream's state.
    """
    new_mode = sdr_demod.resolve_mode(config["mode_setting"], frequency_khz)
    if new_mode == config["mode"]:
        config["frequency_khz"] = frequency_khz
        return demodulator
    new_low_cut_hz, new_high_cut_hz = resolve_ssb_cuts(new_mode, config["low_cut_hz"], config["high_cut_hz"])
    prospective_config = dict(
        config, mode=new_mode, low_cut_hz=new_low_cut_hz, high_cut_hz=new_high_cut_hz, frequency_khz=frequency_khz,
    )
    new_demodulator = sdr_demod.build_demodulator(
        prospective_config, config["backend"], iq_sample_rate_hz, sample_rate_hz, ssb_modes,
    )
    config["frequency_khz"] = frequency_khz
    config["mode"] = new_mode
    config["low_cut_hz"], config["high_cut_hz"] = new_low_cut_hz, new_high_cut_hz
    return new_demodulator


def apply_retune_safely(config, iq_sample_rate_hz, sample_rate_hz, ssb_modes, frequency_khz, demodulator, set_frequency_hz):
    """Wrap apply_tuned_frequency (and the caller-supplied set_frequency_hz,
    which actually retunes the hardware) so that neither a real SoapySDR
    setFrequency() rejection -- a real risk here, since a PlutoSDR's
    AD936x front end may reject HF frequencies without a modified image,
    and the dialplan accepts the full 1800-29999 kHz range -- nor a
    demodulator-rebuild failure inside apply_tuned_frequency can escape
    and take down the shared MOH stream for every other listener over a
    single caller's bad retune (see docs/superpowers/plans/
    2026-07-30-caller-tuning.md's Global Constraints). Returns the
    demodulator to keep using: the new one on success, the original,
    still-valid one on failure.

    apply_tuned_frequency commits its config mutation as soon as the new
    demodulator is built successfully -- before set_frequency_hz has run.
    If set_frequency_hz then rejects the frequency, config would otherwise
    be left describing a mode/frequency the hardware was never actually
    tuned to, while the demodulator we keep using (the old one, returned
    below) still matches the old, still-actually-tuned frequency. Snapshot
    config's tunable fields up front and restore them on any failure --
    from apply_tuned_frequency or from set_frequency_hz alike -- so config,
    the returned demodulator, and the real hardware state always agree.

    set_frequency_hz is a callable(frequency_hz) rather than a raw
    device/SoapySDR reference so this function has no dependency on the
    SoapySDR native module -- main() only imports SoapySDR lazily so the
    rest of this module stays importable (for tests, for example) in
    environments without the SoapySDR python bindings installed.
    """
    previous_state = {key: config[key] for key in ("frequency_khz", "mode", "low_cut_hz", "high_cut_hz")}
    try:
        new_demodulator = apply_tuned_frequency(
            config, iq_sample_rate_hz, sample_rate_hz, ssb_modes, frequency_khz, demodulator,
        )
        set_frequency_hz(frequency_khz * 1000.0)
    except Exception as error:
        config.update(previous_state)
        print(
            f"SDR_RETUNE_FAILED frequency_khz={frequency_khz:g} error={error}",
            file=sys.stderr, flush=True,
        )
        return demodulator
    write_current_frequency(frequency_khz)
    print(
        f"SDR_RETUNED frequency_khz={frequency_khz:g} mode={config['mode']}",
        file=sys.stderr, flush=True,
    )
    return new_demodulator


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
        config["low_cut_hz"], config["high_cut_hz"] = resolve_ssb_cuts(mode, magnitude_low, magnitude_high)
    elif mode in sdr_demod.FM_MODES:
        if mode == "wfm":
            default_deviation_hz, default_channel_bandwidth_hz, default_deemphasis_us = 75000, 200000, 50.0
        else:
            default_deviation_hz, default_channel_bandwidth_hz, default_deemphasis_us = 5000, 16000, None

        deviation_hz = env_float("SDR_FM_DEVIATION_HZ", default_deviation_hz)
        channel_bandwidth_hz = env_float("SDR_FM_CHANNEL_BANDWIDTH_HZ", default_channel_bandwidth_hz)
        if deviation_hz <= 0:
            raise ValueError("SDR_FM_DEVIATION_HZ must be positive")
        if channel_bandwidth_hz <= 0:
            raise ValueError("SDR_FM_CHANNEL_BANDWIDTH_HZ must be positive")

        squelch_db, squelch_hang_ms = _load_squelch_config()

        # deemphasis_us: nfm defaults to None ("feature disabled"), matching
        # FmStreamingDemodulator's own optional-parameter contract, since
        # two-way FM de-emphasis standards vary too much to guess. wfm gets
        # a real default since broadcast FM's de-emphasis is standardized
        # by region (50us EU/Finland, 75us US) -- either way, an explicit
        # SDR_FM_DEEMPHASIS_US always wins.
        deemphasis_setting = os.environ.get("SDR_FM_DEEMPHASIS_US", "").strip()
        if deemphasis_setting:
            deemphasis_us = float(deemphasis_setting)
        elif default_deemphasis_us is not None:
            deemphasis_us = default_deemphasis_us
        else:
            deemphasis_us = None
        if deemphasis_us is not None and deemphasis_us <= 0:
            raise ValueError("SDR_FM_DEEMPHASIS_US must be positive")

        config.update({
            "deviation_hz": deviation_hz,
            "channel_bandwidth_hz": channel_bandwidth_hz,
            "squelch_db": squelch_db,
            "squelch_hang_ms": squelch_hang_ms,
            "deemphasis_us": deemphasis_us,
        })
    elif mode == "am":
        channel_bandwidth_hz = env_float("SDR_AM_CHANNEL_BANDWIDTH_HZ", 25000)
        if channel_bandwidth_hz <= 0:
            raise ValueError("SDR_AM_CHANNEL_BANDWIDTH_HZ must be positive")

        squelch_db, squelch_hang_ms = _load_squelch_config()

        config.update({
            "channel_bandwidth_hz": channel_bandwidth_hz,
            "squelch_db": squelch_db,
            "squelch_hang_ms": squelch_hang_ms,
        })
    else:
        raise AssertionError(f"unhandled mode {mode!r}: MODE_CHOICES/SSB_MODES may be out of sync")

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


def write_current_frequency(frequency_khz, path=CURRENT_FREQUENCY_PATH):
    """Publish the actually-tuned frequency for the dialplan's tune-menu
    to read back via Asterisk's FILE() function (config/
    extensions.conf.template's [tune-menu] extension `s`, which every
    caller reaches, not just those who press a particular digit) -- a
    caller who just wants to listen still gets told what frequency
    they're joining, even after some other caller has retuned it away
    from the .env default. Atomic write (temp + rename) for the same
    reason read_control_frequency tolerates a partial read: this file gets
    written while other calls may be reading it concurrently.

    Formatted with :.0f, not :g: SayNumber() only speaks integers, and :g
    would render a large-enough value (e.g. 1296000) in scientific
    notation ("1.296e+06"), which SayNumber's integer parsing would
    mangle. Unreachable for today's SSB-only 1800-29999 kHz tuning range,
    but this function is also called unconditionally for every mode
    (including wfm's ~six-digit kHz values) at startup.
    """
    temporary = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(f"{frequency_khz:.0f}", encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        print(f"SDR_CURRENT_FREQUENCY_WRITE_FAILED error={error}", file=sys.stderr, flush=True)


def read_control_frequency(control_path, last_mtime):
    """Check control_path for a frequency written since last_mtime (by
    sdr_tune.py, from the dialplan's tune IVR). Returns (mtime,
    frequency_khz) if a new, parseable value was found, or (last_mtime or
    the file's current mtime, None) otherwise -- a missing file, an
    unchanged mtime, or unparseable content are all treated the same way:
    "no retune this iteration," never a crash, since a bad write must not
    take down every listener's shared stream.
    """
    try:
        mtime = control_path.stat().st_mtime
    except OSError:
        return last_mtime, None
    if mtime == last_mtime:
        return last_mtime, None
    try:
        frequency_khz = float(control_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return mtime, None
    return mtime, frequency_khz


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
    audio_gain = env_float("SDR_AUDIO_GAIN", default_audio_gain_for_mode(config["mode"]))
    write_current_frequency(config["frequency_khz"])

    demodulator = None
    stopping = False
    device = None
    stream = None
    iq_sample_rate_hz = None
    pcm_buffer = bytearray()
    last_audio = 0.0
    last_status = 0.0
    last_tune_mtime = 0.0
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
                    # Read config["frequency_khz"] fresh on every (re)connect --
                    # never cache it in a pre-loop local -- since a caller
                    # retune (apply_tuned_frequency) can change it in place at
                    # any point while a device is connected, and a later
                    # reconnect (e.g. after SDR_STALE_SECONDS or a read
                    # exception) must tune the hardware to wherever the
                    # listener currently is, not back to the process's
                    # original startup frequency.
                    device, stream, iq_sample_rate_hz = backend.open_device(
                        config["frequency_khz"] * 1000.0, config["mode"], config["backend_config"],
                    )
                    print(
                        f"SDR_CONNECT_RATE iq_sample_rate_hz={iq_sample_rate_hz:g}",
                        file=sys.stderr, flush=True,
                    )
                except Exception as error:
                    print(f"SDR_DEVICE_OPEN_FAILED error={error}", file=sys.stderr, flush=True)
                    device = None
                    stream = None
                    next_start = now + backoff
                    backoff = min(backoff * 2, maximum_backoff)

                if device is not None and demodulator is None:
                    # A failure here (bad rate, non-integer decimation, or an
                    # unbuildable filter design) is a configuration/hardware-
                    # compatibility problem, not a transient device error, so it
                    # must NOT be swallowed by the retry logic above: close the
                    # just-opened device and let it propagate all the way out to
                    # the __main__ guard's SDR_CONFIG_ERROR handler.
                    try:
                        demodulator = sdr_demod.build_demodulator(
                            config, backend, iq_sample_rate_hz, SAMPLE_RATE, SSB_MODES,
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

                # Guarded on device is not None: disconnect() (called just
                # above, on a read exception) sets device = None via its
                # nonlocal closure, but doesn't exit this still-active
                # `if device is not None:` block (that condition was only
                # checked once, at the top) -- without this guard, a
                # control-file frequency pending in the same iteration
                # would call device.setFrequency() on a None device.
                if device is not None and config["mode"] in SSB_MODES:
                    last_tune_mtime, new_frequency_khz = read_control_frequency(
                        TUNE_CONTROL_PATH, last_tune_mtime,
                    )
                    if new_frequency_khz is not None and TUNE_MIN_FREQUENCY_KHZ <= new_frequency_khz <= TUNE_MAX_FREQUENCY_KHZ:
                        demodulator = apply_retune_safely(
                            config, iq_sample_rate_hz, SAMPLE_RATE, SSB_MODES, new_frequency_khz, demodulator,
                            lambda hz: device.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, hz),
                        )
            else:
                time.sleep(max(0.0, min(FRAME_SECONDS, deadline - now)))

            now = time.monotonic()
            device_stale = device is not None and now - last_audio > stale_seconds
            if device_stale:
                disconnect("audio_stale")

            while now >= deadline:
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
