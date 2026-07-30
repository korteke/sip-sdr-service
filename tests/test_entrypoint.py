import os
import subprocess
import unittest
from pathlib import Path

RESOLVE_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "resolve_entry_context.sh"


class ResolveEntryContextTests(unittest.TestCase):
    """Behavioral coverage for docker-entrypoint.sh's SDR_CALLER_TUNING ->
    SDR_ENTRY_CONTEXT resolution, called for explicitly in
    docs/superpowers/specs/2026-07-30-caller-tuning-design.md's Testing
    section. docker-entrypoint.sh itself can't run standalone outside the
    built container image (it expects to runuser as asterisk, render
    Asterisk configs under /etc/asterisk, optionally start the SDRplay API
    service, and exec asterisk), so this exercises the actual shell
    snippet it sources for this one decision -- scripts/
    resolve_entry_context.sh -- run for real under `sh`, rather than
    reimplementing its logic in Python or only checking syntax.
    """

    def _run(self, *env_assignments):
        env = os.environ.copy()
        env.pop("SDR_CALLER_TUNING", None)
        env.pop("SDR_MODE", None)
        script_lines = ["set -eu"]
        script_lines.extend(env_assignments)
        script_lines.append(f'. "{RESOLVE_SCRIPT}"')
        script_lines.append('echo "$SDR_ENTRY_CONTEXT"')
        return subprocess.run(
            ["sh", "-c", "\n".join(script_lines)],
            capture_output=True, text=True, env=env,
        )

    def test_unset_caller_tuning_resolves_to_play_sdr(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "play-sdr")

    def test_off_resolves_to_play_sdr(self):
        result = self._run("SDR_CALLER_TUNING=off")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "play-sdr")

    def test_off_resolves_to_play_sdr_even_with_a_non_ssb_mode(self):
        result = self._run("SDR_CALLER_TUNING=off", "SDR_MODE=wfm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "play-sdr")

    def test_on_with_an_ssb_mode_resolves_to_tune_menu(self):
        for mode in ("lsb", "usb", "auto"):
            with self.subTest(mode=mode):
                result = self._run("SDR_CALLER_TUNING=on", f"SDR_MODE={mode}")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "tune-menu")

    def test_on_with_unset_mode_defaults_to_lsb_and_resolves_to_tune_menu(self):
        result = self._run("SDR_CALLER_TUNING=on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "tune-menu")

    def test_invalid_caller_tuning_value_exits_64(self):
        result = self._run("SDR_CALLER_TUNING=maybe")
        self.assertEqual(result.returncode, 64)

    def test_on_with_a_non_ssb_mode_exits_64(self):
        for mode in ("nfm", "wfm", "am"):
            with self.subTest(mode=mode):
                result = self._run("SDR_CALLER_TUNING=on", f"SDR_MODE={mode}")
                self.assertEqual(result.returncode, 64, result.stderr)


if __name__ == "__main__":
    unittest.main()
