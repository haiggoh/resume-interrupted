#!/usr/bin/env python3
"""Framework-free test that the `shortcuts` file declares real executables.

Usage: python3 tests/test_shortcuts.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED = ["interrupted"]


def declared():
    with open(os.path.join(ROOT, "shortcuts")) as f:
        return [line.split("#")[0].strip() for line in f if line.split("#")[0].strip()]


def main():
    try:
        names = declared()
    except OSError as exc:
        print(f"  FAIL: cannot read shortcuts file: {exc}")
        return 1
    failures = []
    if names != EXPECTED:
        failures.append(f"shortcuts lists {names}, expected {EXPECTED}")
    for name in names:
        target = os.path.join(ROOT, "bin", name)
        if not (os.path.isfile(target) and os.access(target, os.X_OK)):
            failures.append(f"bin/{name} is missing or not executable")
    for failure in failures:
        print(f"  FAIL: {failure}")
    if not failures:
        print("  PASS: shortcuts declares real executables")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
