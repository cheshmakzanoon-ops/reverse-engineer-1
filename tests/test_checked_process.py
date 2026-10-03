"""Regression tests for commands that log failure but still exit successfully."""
from pathlib import Path
import sys
import tempfile
import unittest

from scripts.checked_process import evaluate_output, run_checked


class OutputTests(unittest.TestCase):
    def test_clean_success(self):
        self.assertEqual(evaluate_output(0, "Godot\nPASS: 4   FAIL: 0\n", "PASS:"), (0, ""))

    def test_error_with_zero_exit_is_failure(self):
        for message in ("ERROR: resource missing", "SCRIPT ERROR: Invalid call", "USER ERROR: broken"):
            with self.subTest(message=message):
                self.assertEqual(evaluate_output(0, message)[0], 1)

    def test_ansi_does_not_hide_script_error(self):
        self.assertEqual(evaluate_output(0, "\x1b[31mSCRIPT ERROR: Parse Error\x1b[0m\n")[0], 1)

    def test_warning_is_not_error(self):
        self.assertEqual(evaluate_output(0, "WARNING: running as root\n")[0], 0)

    def test_failed_summary_with_zero_exit_is_failure(self):
        self.assertEqual(evaluate_output(0, "PASS: 4   FAIL: 2\n")[0], 1)

    def test_missing_required_marker_is_failure(self):
        self.assertEqual(evaluate_output(0, "startup only\n", "PASS:")[0], 1)

    def test_nonzero_exit_is_preserved(self):
        self.assertEqual(evaluate_output(7, "PASS: 4   FAIL: 0", "PASS:")[0], 7)


class ProcessTests(unittest.TestCase):
    def invoke(self, code, **kwargs):
        return run_checked([sys.executable, "-S", "-c", code], **kwargs)

    def test_stdout_and_stderr_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "nested" / "run.log"
            result = self.invoke("import sys; print('out'); print('err', file=sys.stderr)", log_path=log)
            self.assertEqual(result.code, 0)
            self.assertIn("out", log.read_text())
            self.assertIn("err", log.read_text())

    def test_real_logged_error_does_not_pass(self):
        result = self.invoke("print('SCRIPT ERROR: missing method')")
        self.assertEqual(result.code, 1)

    def test_timeout_keeps_partial_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "timeout.log"
            result = self.invoke("import time; print('started', flush=True); time.sleep(10)",
                                 timeout=0.15, log_path=log)
            self.assertEqual(result.code, 124)
            self.assertIn("started", log.read_text())

    def test_missing_executable(self):
        result = run_checked(["/definitely/not/a/real/executable"])
        self.assertEqual(result.code, 127)
        self.assertTrue(result.reason)

    def test_invalid_timeout(self):
        with self.assertRaises(ValueError):
            self.invoke("pass", timeout=0)

    def test_empty_command(self):
        with self.assertRaises(ValueError):
            run_checked([])


if __name__ == "__main__":
    unittest.main()
