#!/usr/bin/env python3
"""Run a build/test command, rejecting logged Godot failures even on exit 0.

Example:
    python3 scripts/checked_process.py --log verification/import.log -- \
        godot --headless --path port --import

No optional dependencies; logs retain both stdout and stderr. A timeout kills
all child processes on POSIX so a hung engine does not survive the test runner.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from typing import Sequence

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ENGINE_ERROR = re.compile(r"^\s*(?:SCRIPT ERROR|USER ERROR|ERROR):", re.MULTILINE)
_FAILED_SUMMARY = re.compile(r"\bFAIL:\s*[1-9]\d*\b")


@dataclass(frozen=True)
class CheckedResult:
    code: int
    reason: str
    output: str


def evaluate_output(returncode: int, output: str, required: str | None = None) -> tuple[int, str]:
    """Return a checked exit code and explanation without discarding evidence."""
    if returncode:
        return (returncode if returncode > 0 else 128 - returncode,
                f"process exited with status {returncode}")
    clean = _ANSI.sub("", output)
    if _ENGINE_ERROR.search(clean):
        return 1, "engine logged an error despite exiting successfully"
    if _FAILED_SUMMARY.search(clean):
        return 1, "test summary contains failed checks"
    if required is not None and re.search(required, clean, re.MULTILINE) is None:
        return 1, f"required completion marker not found: {required}"
    return 0, ""


def run_checked(command: Sequence[str], *, timeout: float = 300,
                log_path: Path | None = None, required: str | None = None) -> CheckedResult:
    if not command:
        raise ValueError("a command is required")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if required is not None:
        re.compile(required)  # Reject malformed markers before starting work.
    try:
        process = subprocess.Popen(list(command), stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, errors="replace",
                                   start_new_session=(os.name == "posix"))
        try:
            output, _ = process.communicate(timeout=timeout)
            code, reason = evaluate_output(process.returncode, output, required)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            output, _ = process.communicate()
            code, reason = 124, f"command exceeded {timeout:g} seconds"
    except OSError as exc:
        output = str(exc) + "\n"
        code, reason = 127, "command could not be started"
    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(output, encoding="utf-8")
    return CheckedResult(code, reason, output)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--require", help="regex required in successful output")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        result = run_checked(command, timeout=args.timeout, log_path=args.log,
                             required=args.require)
    except (ValueError, re.error) as exc:
        parser.error(str(exc))
    print(result.output, end="", flush=True)
    if result.reason:
        print(f"CHECK FAILED: {result.reason}", file=sys.stderr)
    return result.code


if __name__ == "__main__":
    raise SystemExit(main())
