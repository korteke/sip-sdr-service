#!/usr/bin/env python3
import json
import subprocess
import time
from pathlib import Path

status_path = Path("/run/sip-sdr/stream.json")

asterisk = subprocess.run(
    ["asterisk", "-rx", "core show uptime"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    check=False,
)
if asterisk.returncode != 0:
    raise SystemExit(1)

# The custom MOH source may not start until the first call. Once it has started,
# require recent status updates and reject a permanently stopped source.
if status_path.exists():
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if time.time() - float(status["updated_epoch"]) > 90:
            raise SystemExit(1)
        if status.get("state") == "stopped":
            raise SystemExit(1)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        raise SystemExit(1)
