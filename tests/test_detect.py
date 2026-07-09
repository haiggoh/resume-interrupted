#!/usr/bin/env python3
"""Framework-free tests for the resume-interrupted detector.

Runs the classify() logic against synthetic transcripts covering the real shapes we
verified against live data: a limit kill, a stalled prompt, a bare probe, a clean
session, and an orphaned last-prompt. No third-party deps.

Usage: python3 tests/test_detect.py
"""
import os, sys, json, tempfile, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "detect", os.path.join(HERE, "..", "hooks", "detect-interrupted.py"))
detect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detect)

passed = 0
failed = 0


def check(cond, label):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS:", label)
    else:
        failed += 1
        print("  FAIL:", label)


def write(recs):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    return path


def U(text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def A(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def TR():
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "out"}]}}


def LP(text):
    return {"type": "last-prompt", "lastPrompt": text}


print("== (E) limit kill: last assistant turn is the budget error ==")
p = write([U("do the thing"), A("working on it"),
           A("API Error: Request rejected (429) · Budget has been exceeded!"), LP("do the thing")])
intr, work, dang = detect.classify(p)
check(intr and work, "flagged as interrupted with work")

print("== (S) stalled: last human prompt unanswered, with prior work ==")
p = write([U("first task"), A("done"), U("wait, one more thing"), LP("wait, one more thing")])
intr, work, dang = detect.classify(p)
check(intr and work, "flagged as interrupted with work")
check("one more thing" in dang, "dangling prompt captured")

print("== bare probe: unanswered prompt, NO prior assistant work -> has_work False ==")
p = write([U("are we back yet?"), LP("are we back yet?")])
intr, work, dang = detect.classify(p)
check(intr and not work, "interrupted but no work (probe -> will be skipped)")

print("== clean session: last human prompt answered ==")
p = write([U("hello"), A("hi"), U("thanks"), A("you're welcome"), LP("thanks")])
intr, work, dang = detect.classify(p)
check((not intr) and work, "not interrupted; has work")

print("== command-output tail is NOT a human prompt (no false positive) ==")
p = write([U("install it"), A("installing"), A("done"),
           {"type": "user", "message": {"role": "user",
            "content": [{"type": "text", "text": "<local-command-stdout>Installed.</local-command-stdout>"}]}},
           LP("install it")])
intr, work, dang = detect.classify(p)
check((not intr) and work, "command-stdout tail treated as clean")

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
