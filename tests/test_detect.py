#!/usr/bin/env python3
"""Framework-free tests for the resume-interrupted detector (v0.2.4).

Covers classify() on the real transcript shapes, plus the two script modes: the auto
SessionStart banner and the --list browse. No third-party deps.

Usage: python3 tests/test_detect.py
"""
import os, sys, json, tempfile, subprocess, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "hooks", "detect-interrupted.py")
spec = importlib.util.spec_from_file_location("detect", SCRIPT)
detect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detect)

passed = 0
failed = 0


def check(cond, label):
    global passed, failed
    if cond:
        passed += 1; print("  PASS:", label)
    else:
        failed += 1; print("  FAIL:", label)


def U(t):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": t}]}}


def A(t):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": t}]}}


def LP(t):
    return {"type": "last-prompt", "lastPrompt": t}


def AERR(t):
    """Assistant record flagged as a real API error turn (client sets isApiErrorMessage)."""
    return {"type": "assistant", "isApiErrorMessage": True,
            "message": {"role": "assistant", "content": [{"type": "text", "text": t}]}}


def session(recs, mtime=None):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.jsonl")
    with open(p, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    return p


BUDGET = "API Error: Request rejected (429) · Budget has been exceeded!"

print("== classify() shapes ==")
c = detect.classify(session([U("do it"), A("ok"), A(BUDGET), LP("do it")]))
check(c["interrupted"] and c["has_work"] and c["reason"] == "limit-kill", "(E) limit kill")
c = detect.classify(session([U("first"), A("done"), U("one more thing"), LP("one more thing")]))
check(c["interrupted"] and c["has_work"] and c["reason"] == "stalled", "(S) stalled, with work")
c = detect.classify(session([U("are we back?"), LP("are we back?")]))
check(c["interrupted"] and not c["has_work"], "bare probe -> interrupted but no work")
c = detect.classify(session([U("hi"), A("hi"), U("thanks"), A("np"), LP("thanks")]))
check((not c["interrupted"]) and c["has_work"], "clean session")
c = detect.classify(session([U("go"), A("working"), A("done"),
      {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "<local-command-stdout>x</local-command-stdout>"}]}}, LP("go")]))
check(not c["interrupted"], "command-stdout tail is not a dangling human prompt")

# Regression (reproduces the f100ee4c false positive): a healthy final turn that DISCUSSES
# the budget-error phrase mid-text must NOT be read as a limit kill. Before the anchored
# fix, `"Budget has been exceeded" in text` matched this and flagged a completed session.
DISCUSS = ("Here's how to tell them apart: a real budget kill ends with a terminal "
           "`Budget has been exceeded` turn that stops the session, whereas a transient "
           "429 recovers. The retry countdown alone proves nothing.")
c = detect.classify(session([U("explain the budget error"), A("sure"), A(DISCUSS), LP("explain the budget error")]))
check((not c["interrupted"]) and c["has_work"], "final turn that DISCUSSES an error phrase is not a kill")
# ...and the discussing turn still counts as substantive work (work-undercount fix).
c = detect.classify(session([U("explain"), A(DISCUSS), LP("explain")]))
check(c["has_work"], "a lone turn quoting an error phrase counts as real work")
# Structural marker: isApiErrorMessage alone marks a kill even if the text isn't anchored.
c = detect.classify(session([U("go"), A("working"), AERR("Overloaded — gave up after 10 retries"), LP("go")]))
check(c["interrupted"] and c["reason"] == "limit-kill", "isApiErrorMessage flag alone marks a kill")
# apiErrorStatus alone (no isApiErrorMessage, no matching wording) also marks a kill —
# wording-agnostic structural detection, so error-message changes can't cause a false negative.
AS = {"type": "assistant", "apiErrorStatus": 429,
      "message": {"role": "assistant", "content": [{"type": "text", "text": "gateway said no"}]}}
c = detect.classify(session([U("go"), A("working"), AS, LP("go")]))
check(c["interrupted"] and c["reason"] == "limit-kill", "apiErrorStatus alone marks a kill (wording-agnostic)")
# A transient error written mid-session that the session RECOVERED from (a normal turn
# follows) must NOT be read as a kill — only the LAST assistant turn decides (E).
c = detect.classify(session([U("go"), AERR("Overloaded"), A("recovered — here's the answer"), LP("go")]))
check((not c["interrupted"]) and c["has_work"], "recovered mid-session error is not a kill")


def run(argv, stdin=""):
    r = subprocess.run([sys.executable, SCRIPT] + argv, input=stdin,
                       capture_output=True, text=True)
    return r.stdout


def proj_with(files):
    """files: list of (name, recs, mtime_touch). Returns dir."""
    d = tempfile.mkdtemp()
    for name, recs, mt in files:
        p = os.path.join(d, name)
        with open(p, "w") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")
        if mt:
            os.utime(p, (mt, mt))
    return d


WORK = [U("build the thing"), A("starting"), A(BUDGET), LP("build the thing")]
PROBE = [U("are we back yet?"), LP("are we back yet?")]
CLEAN = [U("hi"), A("hi"), U("bye"), A("cya"), LP("bye")]

print("\n== auto mode: banner (systemMessage) + additionalContext on interruption ==")
d = proj_with([("work.jsonl", WORK, 1000), ("probe.jsonl", PROBE, 2000)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
try:
    o = json.loads(out); ok = bool(o.get("systemMessage")) and "additionalContext" in o.get("hookSpecificOutput", {})
except Exception:
    ok = False
check(ok, "emits systemMessage banner + additionalContext")

print("== auto mode: silent when a clean substantive session is newer (moved on) ==")
d = proj_with([("work.jsonl", WORK, 1000), ("clean.jsonl", CLEAN, 3000)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
check(out.strip() == "", "silent after a clean substantive session")

print("== feature 2: killed bare-probe offer session -> re-asks the original ==")
d = proj_with([("work.jsonl", WORK, 1000), ("killed-probe.jsonl", PROBE, 2000)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
check(bool(out.strip()) and "build the thing" in out, "re-asks the original work session")

print("\n== v0.2.3: banner wording is reason-aware (E limit-kill vs S stalled) ==")
# (E) limit-kill: the prompt WAS answered and the session died mid-work afterward, so the
# banner must NOT claim the request "was never completed" — it must name the usage/API limit.
d = proj_with([("k.jsonl", WORK, 1000)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
banner = json.loads(out).get("systemMessage", "")
check("never completed" not in banner and ("limit" in banner.lower() or "api" in banner.lower()),
      "(E) banner names a usage/API limit, not 'never completed'")
# (S) stalled: the request genuinely received no reply -> 'unanswered' is the accurate framing.
STALL = [U("first"), A("done"), U("one more thing"), LP("one more thing")]
d = proj_with([("s.jsonl", STALL, 1000)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
banner = json.loads(out).get("systemMessage", "")
check("unanswered" in banner.lower(), "(S) banner says the request was left unanswered")

print("== v0.2.3: a long quote truncates on a word boundary with an ellipsis (no mid-word cut) ==")
import re
LONG = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
        "mike november oscar papa quebec romeo sierra tango")
d = proj_with([("k.jsonl", [U(LONG), A("ok"), A(BUDGET), LP(LONG)], 1000)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
banner = json.loads(out).get("systemMessage", "")
quoted = (re.search(r'"([^"]*)"', banner) or re.search(r'(alpha[^"]*)', banner))
quoted = quoted.group(1) if quoted else ""
check(quoted.endswith("…"), "long quote ends with an ellipsis")
body = quoted.rstrip("… ").rstrip()
check(bool(body) and LONG.startswith(body) and (len(body) == len(LONG) or LONG[len(body)] == " "),
      "quote cut on a word boundary (prefix ends exactly at a space)")
check(70 < len(body) <= 100, "quote cap raised to ~100 chars (v0.2.4), still ~one line")

print("== --list: shows probes too, marks the recommended substantive one ==")
d = proj_with([("work.jsonl", WORK, 1000), ("probe.jsonl", PROBE, 2000)])
out = run(["--list", "--dir", d])
check("RECOMMENDED" in out, "marks a recommendation")
check("are we back yet?" in out, "probe prompt shown (transparency)")
check("[probe]" in out and "[work" in out, "labels work vs probe")

print("== never blocks: garbage stdin exits cleanly, no output ==")
out = run([], stdin="not json")
check(out.strip() == "", "garbage stdin -> no output")

print("\n== v0.2.7: banner is a boxed WARNING (rules + caps header), multi-line ==")
d = proj_with([("k.jsonl", WORK, 1000)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
banner = json.loads(out).get("systemMessage", "")
check("\n" in banner, "banner is multi-line (boxed)")
check("━" in banner, "banner has a horizontal rule")
check("INTERRUPTED SESSION" in banner, "banner has a caps header")
check("⚡" in banner, "banner keeps the ⚡ identity mark")
# reason-awareness + quoting preserved under the new layout
check("never completed" not in banner and ("limit" in banner.lower() or "api" in banner.lower()),
      "(E) boxed banner still names a usage/API limit")
check(bool(re.search(r'"[^"]+"', banner)), "boxed banner still quotes the dangling prompt")

print("\n== v0.2.7: queued_prompts harvests ALL unanswered notes (skips probe noise) ==")
Q = [U("build the thing"), A("starting"),
     U("also add logging"), U("are we back yet?"), U("and write the docs"), U("still there?"),
     LP("still there?")]
check(detect.queued_prompts(Q) == ["also add logging", "and write the docs"],
      "harvests both real queued notes in order, drops probes")
check(detect.queued_prompts([U("are we back?"), LP("are we back?")]) == [],
      "pure probe session -> no queued notes")
check(detect.queued_prompts([U("hi"), A("hi"), U("bye"), A("cya")]) == [],
      "clean answered session -> no queued notes")
QK = [U("build"), A("starting"), AERR(BUDGET), U("note one"), AERR(BUDGET), U("note two"), LP("note two")]
check(detect.queued_prompts(QK) == ["note one", "note two"],
      "harvests notes interleaved with limit-kill error turns")

print("== v0.2.7: --list surfaces every queued note for promotion ==")
d = proj_with([("multi.jsonl", Q, 1000)])
out = run(["--list", "--dir", d])
check("also add logging" in out and "and write the docs" in out, "--list shows all queued notes")

print("== v0.2.7: auto additionalContext carries the queued notes ==")
d = proj_with([("multi.jsonl", Q, 1000)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
ctx = json.loads(out).get("hookSpecificOutput", {}).get("additionalContext", "")
check("also add logging" in ctx and "and write the docs" in ctx, "additionalContext lists queued notes for promotion")

print("\n== v0.2.8: cross-plugin banner-order flag (optional, one-way signal) ==")
flag_root = tempfile.mkdtemp()
os.environ["TMPDIR"] = flag_root


def flag_path(sid):
    return os.path.join(flag_root, "claude-sessionstart-banners", "%s.resume-interrupted.done" % sid)


d = proj_with([("clean.jsonl", CLEAN, 1000)])
run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "sid-clean"}))
check(os.path.exists(flag_path("sid-clean")), "flag written even when there's nothing to print")
check("printed=0" in open(flag_path("sid-clean")).read(), "flag records printed=0 when banner was skipped")

d = proj_with([("work.jsonl", WORK, 1000)])
run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "sid-work"}))
check(os.path.exists(flag_path("sid-work")), "flag written when a banner is printed")
check("printed=1" in open(flag_path("sid-work")).read(), "flag records printed=1 when banner was printed")

run([], stdin="not json")
check(not os.path.exists(flag_path("")), "unparseable stdin writes no flag (no session id to key on)")

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
