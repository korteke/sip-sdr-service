import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("sdr_tune", "scripts/sdr_tune.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WriteControlFrequencyTests(unittest.TestCase):
    def test_creates_parent_directory_and_file(self):
        control_path = Path(tempfile.mkdtemp()) / "nested" / "tune-frequency"
        MODULE.write_control_frequency("14074", control_path)
        self.assertEqual(control_path.read_text(encoding="utf-8").strip(), "14074")

    def test_overwrite_leaves_no_leftover_temp_file(self):
        control_path = Path(tempfile.mkdtemp()) / "tune-frequency"
        MODULE.write_control_frequency("3699", control_path)
        MODULE.write_control_frequency("14074", control_path)
        self.assertEqual(control_path.read_text(encoding="utf-8").strip(), "14074")
        leftovers = list(control_path.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])


class TempControlPathTests(unittest.TestCase):
    """A shared temp filename (e.g. plain .with_suffix('.tmp')) would let
    two concurrently-tuning callers' separate sdr_tune.py invocations
    clobber each other's write before either renames -- not a crash, but
    a silently dropped retune, contradicting this module's own atomic-
    write claim (true only for a single writer). The temp filename must
    be unique per invoking process.
    """

    def test_temp_path_includes_the_current_process_id(self):
        control_path = Path("/run/sip-sdr/tune-frequency")
        temporary = MODULE._temp_control_path(control_path)
        self.assertEqual(temporary.name, f"tune-frequency.{os.getpid()}.tmp")

    def test_temp_path_for_a_different_process_id_would_differ(self):
        control_path = Path("/run/sip-sdr/tune-frequency")
        temporary = MODULE._temp_control_path(control_path)
        self.assertNotEqual(temporary.name, "tune-frequency.tmp")
        self.assertIn(str(os.getpid()), temporary.name)


class MainValidationTests(unittest.TestCase):
    """sys.argv[1] used to be passed to write_control_frequency completely
    unguarded: no arguments raised an uncaught IndexError, and an
    unparseable or out-of-range value was written to the control file
    as-is (sdr_stream.py's read_control_frequency would then silently
    drop it on the next poll -- no crash there, but a confirmation prompt
    that already told the caller "Tuning to N kilohertz" that never
    actually took effect, without even a log line explaining why).
    """

    def test_missing_argument_returns_nonzero_without_writing(self):
        with patch.object(MODULE, "write_control_frequency") as write_mock:
            exit_code = MODULE.main(["sdr_tune.py"])
        self.assertNotEqual(exit_code, 0)
        write_mock.assert_not_called()

    def test_non_numeric_argument_returns_nonzero_without_writing(self):
        with patch.object(MODULE, "write_control_frequency") as write_mock:
            exit_code = MODULE.main(["sdr_tune.py", "not-a-number"])
        self.assertNotEqual(exit_code, 0)
        write_mock.assert_not_called()

    def test_out_of_range_argument_returns_nonzero_without_writing(self):
        with patch.object(MODULE, "write_control_frequency") as write_mock:
            exit_code = MODULE.main(["sdr_tune.py", "99999"])
        self.assertNotEqual(exit_code, 0)
        write_mock.assert_not_called()

    def test_valid_argument_writes_and_returns_zero(self):
        with patch.object(MODULE, "write_control_frequency") as write_mock:
            exit_code = MODULE.main(["sdr_tune.py", "14074"])
        self.assertEqual(exit_code, 0)
        write_mock.assert_called_once_with("14074")


if __name__ == "__main__":
    unittest.main()
