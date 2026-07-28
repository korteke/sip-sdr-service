#!/usr/bin/env python3
"""Shared env-var parsing helpers used by sdr_stream.py and every backend adapter."""

import os


def env_float(name, default):
    return float(os.environ.get(name, default))


def env_choice(name, default, choices):
    value = os.environ.get(name, default).lower()
    if value not in choices:
        raise ValueError(f"{name} must be one of {sorted(choices)}")
    return value
