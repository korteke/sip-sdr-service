#!/usr/bin/env python3
"""Atomically publish a caller-entered tuning frequency for sdr_stream.py's
control-file poll (see sdr_stream.read_control_frequency). Invoked from the
Asterisk dialplan's tune IVR (config/extensions.conf.template, [tune-sdr])
as: python3 sdr_tune.py <frequency_khz>.
"""

import os
import sys
from pathlib import Path

DEFAULT_CONTROL_PATH = Path("/run/sip-sdr/tune-frequency")

# Mirrors sdr_stream.py's TUNE_MIN_FREQUENCY_KHZ/TUNE_MAX_FREQUENCY_KHZ.
# Duplicated rather than imported: this script is invoked via System() on
# every tuning attempt, and importing sdr_stream would drag in numpy/
# sdr_demod (and, transitively, whatever backend the running config
# selects) just to validate a number -- unnecessary weight and a
# fragility risk for a script whose only job is a fast, defense-in-depth
# range check on top of the dialplan's own GotoIf validation.
TUNE_MIN_FREQUENCY_KHZ = 1800.0
TUNE_MAX_FREQUENCY_KHZ = 29999.0


def _temp_control_path(control_path):
    """Include the writer's PID in the temp filename: two callers tuning
    at (nearly) the same moment each invoke a separate sdr_tune.py
    process, and a shared temp filename (e.g. a plain .with_suffix('.tmp'))
    would let one process's write clobber the other's before either
    renames, silently dropping one caller's retune.
    """
    return control_path.with_name(f"{control_path.name}.{os.getpid()}.tmp")


def write_control_frequency(frequency_khz, control_path=DEFAULT_CONTROL_PATH):
    control_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temp_control_path(control_path)
    temporary.write_text(f"{frequency_khz}\n", encoding="utf-8")
    temporary.replace(control_path)


def main(argv):
    if len(argv) != 2:
        print(f"usage: {argv[0]} <frequency_khz>", file=sys.stderr)
        return 64
    raw_frequency_khz = argv[1]
    try:
        frequency_khz = float(raw_frequency_khz)
    except ValueError:
        print(f"SDR_TUNE_INVALID_FREQUENCY value={raw_frequency_khz!r}", file=sys.stderr)
        return 64
    if not (TUNE_MIN_FREQUENCY_KHZ <= frequency_khz <= TUNE_MAX_FREQUENCY_KHZ):
        print(f"SDR_TUNE_OUT_OF_RANGE frequency_khz={frequency_khz:g}", file=sys.stderr)
        return 64
    write_control_frequency(raw_frequency_khz)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
