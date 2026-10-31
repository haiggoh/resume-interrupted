#!/usr/bin/env python3
"""Framework-free tests for the resume-interrupted detector (v0.2.13).

Covers classify() on the real transcript shapes, plus the two script modes: the auto
SessionStart banner and the --list browse. No third-party deps.

Usage: python3 tests/test_detect.py
"""
import io
import os, sys, json, tempfile, subprocess, importlib.util, time

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
# Structural marker: isApiErrorMessage alone marks an error death even if the text isn't
# anchored. The REASON is "api-error", not "limit-kill": an overload is not a quota refusal,
# and calling it one made the resumed session warn about a budget that was never involved.
c = detect.classify(session([U("go"), A("working"), AERR("Overloaded — gave up after 10 retries"), LP("go")]))
check(c["interrupted"] and c["reason"] == "api-error", "isApiErrorMessage flag alone marks an error death")
# apiErrorStatus alone (no isApiErrorMessage, no matching wording) also marks a kill —
# wording-agnostic structural detection, so error-message changes can't cause a false negative.
AS = {"type": "assistant", "apiErrorStatus": 429,
      "message": {"role": "assistant", "content": [{"type": "text", "text": "gateway said no"}]}}
c = detect.classify(session([U("go"), A("working"), AS, LP("go")]))
check(c["interrupted"] and c["reason"] == "limit-kill", "apiErrorStatus alone marks a kill (wording-agnostic)")

# Regression (2026-07-15 false positive): a plain "wrap" prompt gets answered, but the
# reply invokes a skill, which is delivered as a role=user turn with a "Base directory
# for this skill: ..." preamble. Claude Code's last-prompt marker isn't refreshed by that
# skill turn, so it still echoes the ORIGINAL "wrap" text after the reply completes.
# Before the fix, comparing that stale last-prompt against the skill-preamble "last human
# text" (which differs) falsely reported a fresh, unanswered dangler.
SKILL_BODY = "Base directory for this skill: /plugins/cache/some-skill\n\n# Some skill\nBody text..."
c = detect.classify(session([
    U("wrap"), LP("wrap"), U(SKILL_BODY), LP("wrap"), A("Wrap-up complete: did the thing."), LP("wrap"),
]))
check(not c["interrupted"], "stale last-prompt echo after an answered skill turn is not a fresh dangler")
# But a GENUINE stall must still be caught: the user re-invokes a skill and the session
# dies before any reply — even though last-prompt still echoes an older, already-answered
# prompt from earlier in the session (same staleness quirk, opposite outcome).
c = detect.classify(session([
    U("wrap"), A("done with wrap"), LP("do something else"),
    U(SKILL_BODY), LP("do something else"),
]))
check(c["interrupted"] and c["reason"] == "stalled", "genuine stall after a skill re-invocation is still caught")
# A transient error written mid-session that the session RECOVERED from (a normal turn
# follows) must NOT be read as a kill — only the LAST assistant turn decides (E).
c = detect.classify(session([U("go"), AERR("Overloaded"), A("recovered — here's the answer"), LP("go")]))
check((not c["interrupted"]) and c["has_work"], "recovered mid-session error is not a kill")

print("== Pass A (v0.2.16): continuation-stub masking fix + additive fields ==")
# The real bug: an early real human turn with NO genuine reply, masked by a later
# "Continue from where you left off." stub whose filler reply makes it look answered.
c = detect.classify(session([U("real early note"),
                             U("Continue from where you left off."),
                             A("No response requested.")]))
check(c["interrupted"] and c["reason"] == "stalled", "masked early turn is surfaced, not swallowed")
check(c["dangling"] == "real early note", "surfaces the EARLIEST unanswered real turn, not the stub")
check(c["is_downtime_note"] is True, "flags it a downtime note (a stub follows the real turn)")
check(c["has_work"] is False, "a stub's filler reply does not count as genuine work")
# Clean answered session: not a downtime note, reports its genuine work-turn count.
c = detect.classify(session([U("hi"), A("one"), U("more"), A("two"), LP("more")]))
check(c["is_downtime_note"] is False and c["work_count"] == 2, "clean session: not downtime, work_count=2")
# A real trailing dangling turn (NO stub after it) is a primary stall, not a downtime note.
c = detect.classify(session([U("first"), A("done"), U("dangling"), LP("dangling")]))
check(c["interrupted"] and c["reason"] == "stalled" and c["is_downtime_note"] is False,
      "trailing real dangling turn is a primary stall, not a downtime note")
check(c["work_count"] == 1, "work_count returned alongside has_work")


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
check("wt" in out, "--list shows a work-turn count (Pass A item 3)")

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

print("== resume-interrupted-surface fix: visible banner shows queued-note CONTENT, not just a count ==")
d = proj_with([("multi.jsonl", Q, 1000)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
banner = json.loads(out).get("systemMessage", "")
check("also add logging" in banner and "and write the docs" in banner,
      "banner surfaces the actual queued note text, matching _emit_orphaned_queued_notes' style")
check("ask me to surface them" not in banner,
      "banner no longer defers content behind a bare count + ask-to-surface prompt")

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

print("\n== v0.2.9: upstream wait on no-hidden-changes' flag (symmetric, one-way, bounded) ==")
settings_dir = tempfile.mkdtemp()
os.environ["CLAUDE_SETTINGS_FILE"] = os.path.join(settings_dir, "settings.json")
os.environ["RESUME_INTERRUPTED_BANNER_WAIT_S"] = "0.3"
os.environ["RESUME_INTERRUPTED_BANNER_POLL_S"] = "0.02"


def nhc_flag_path(sid):
    return os.path.join(flag_root, "claude-sessionstart-banners", "%s.no-hidden-changes.done" % sid)


def write_settings(enabled):
    with open(os.environ["CLAUDE_SETTINGS_FILE"], "w") as fh:
        json.dump({"enabledPlugins": {"no-hidden-changes@haiggoh": True} if enabled else {}}, fh)


write_settings(enabled=False)
check(not detect._plugin_enabled("no-hidden-changes"), "_plugin_enabled: false when not in enabledPlugins")
write_settings(enabled=True)
check(detect._plugin_enabled("no-hidden-changes"), "_plugin_enabled: true when '<slug>@marketplace' is truthy")
os.environ["CLAUDE_SETTINGS_FILE"] = os.path.join(settings_dir, "missing.json")
check(not detect._plugin_enabled("no-hidden-changes"), "_plugin_enabled: missing settings file fails closed, never raises")
os.environ["CLAUDE_SETTINGS_FILE"] = os.path.join(settings_dir, "settings.json")

write_settings(enabled=True)
sid = "sid-upstream-present"
os.makedirs(os.path.dirname(nhc_flag_path(sid)), exist_ok=True)
open(nhc_flag_path(sid), "w").write("producer=no-hidden-changes\n")
t0 = __import__("time").monotonic()
detect._wait_for_no_hidden_changes(sid)
check(__import__("time").monotonic() - t0 < 0.25, "flag already present -> returns near-instantly, no full wait")

write_settings(enabled=True)
sid = "sid-upstream-absent"
t0 = __import__("time").monotonic()
detect._wait_for_no_hidden_changes(sid)
elapsed = __import__("time").monotonic() - t0
check(0.25 <= elapsed < 1.0, "flag never appears -> waits out BANNER_WAIT_S then falls through (no hang)")

write_settings(enabled=False)
sid = "sid-upstream-disabled"
t0 = __import__("time").monotonic()
detect._wait_for_no_hidden_changes(sid)
check(__import__("time").monotonic() - t0 < 0.1, "no-hidden-changes not enabled -> no wait at all")

detect._wait_for_no_hidden_changes("")
check(True, "empty session id -> no-op, never raises")

print("\n== v0.2.13: orphaned queued notes from dead-end probes AFTER a clean session ==")
# Real-world shape (confirmed against this project's own transcripts): a clean substantive
# session happens, THEN the connection dies leaving no trace, and the user's retries land in
# a string of new, newer, has_work=False probe sessions -- one of which carries a real note
# ("heads up: I manually fixed a bug you created, already pushed") that never got a reply.
# mtimes are anchored to real "now" since the fix's recency window is wall-clock-relative.
NOW = time.time()
HOUR = 60 * 60
PROBE_WITH_NOTE = [U("heads up: I manually fixed a bug you created, already pushed"),
                   LP("heads up: I manually fixed a bug you created, already pushed")]
BARE_PROBE = [U("are we back yet?"), LP("are we back yet?")]
d = proj_with([
    ("clean.jsonl", CLEAN, NOW - 3 * HOUR),          # newest substantive session: clean
    ("probe1.jsonl", BARE_PROBE, NOW - 2 * HOUR),    # dead-end retry, no content
    ("probe2.jsonl", PROBE_WITH_NOTE, NOW - 1 * HOUR),  # dead-end retry, has a real note
])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
check(bool(out.strip()), "orphaned-note case (probes AFTER the clean top) is no longer fully silent")
o = json.loads(out) if out.strip() else {}
banner = o.get("systemMessage", "")
ctx = o.get("hookSpecificOutput", {}).get("additionalContext", "")
check("manually fixed a bug" in banner, "banner surfaces the note queued in a later dead-end probe")
check("manually fixed a bug" in ctx, "additionalContext carries the orphaned queued note")
check("QUEUED NOTES" in banner and "INTERRUPTED SESSION" not in banner,
      "uses the distinct queued-notes notice, NOT the full resume banner")

print("== v0.2.13: suppression preserved when nothing interesting is queued in later probes ==")
# Bare probes only after the clean top -> still fully silent (no regression, no nagging).
d = proj_with([("clean.jsonl", CLEAN, NOW - 2 * HOUR),
               ("probe1.jsonl", BARE_PROBE, NOW - 1 * HOUR)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
check(out.strip() == "", "bare probes after the clean top -> stays silent (suppression preserved)")
# Also: purely clean history stays silent.
d = proj_with([("c1.jsonl", CLEAN, NOW - 2 * HOUR), ("c2.jsonl", CLEAN, NOW - 1 * HOUR)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
check(out.strip() == "", "all-clean history -> stays silent")
# No probes at all after a clean top (nothing newer) -> stays silent.
d = proj_with([("clean.jsonl", CLEAN, NOW - 1 * HOUR)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
check(out.strip() == "", "clean top with nothing newer -> stays silent")

print("== v0.2.13: bounded recency window -- an ancient dead-end probe note is NOT resurrected ==")
# Same note, but the probe is far outside the window (anchored to now, not to the clean top).
DAY = 24 * HOUR
d = proj_with([("clean.jsonl", CLEAN, NOW - 40 * DAY),
               ("probe.jsonl", PROBE_WITH_NOTE, NOW - 39 * DAY)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
check(out.strip() == "", "probe note older than the recency window is not resurrected")

print("== v0.2.13: walk stops at the first has_work=True session in the newer range ==")
# The NEWEST substantive session (whatever it is) is what _recommended() actually keys on --
# so to test "the orphaned-notes walk stops at a has_work=True session encountered along the
# way", that has_work=True session must itself be the newest substantive one (i.e. the
# suppressing clean top), with a note-bearing probe BETWEEN it and an even-older interrupted
# has_work=True session. The walk (files[:top_idx]) never reaches past top_idx at all, so the
# older interrupted session's notes are correctly out of scope for this secondary path --
# this exercises that top_idx itself is found correctly even with a probe carrying a note
# sitting behind an older has_work=True session that could otherwise confuse a naive scan.
d = proj_with([
    ("older_work.jsonl", WORK, NOW - 4 * HOUR),           # has_work=True, interrupted (older)
    ("probe_before_top.jsonl", PROBE_WITH_NOTE, NOW - 3 * HOUR),  # note, but OLDER than the clean top
    ("clean_top.jsonl", CLEAN, NOW - 2 * HOUR),           # newest substantive session: clean
])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
check(out.strip() == "", "note in a probe OLDER than the clean top is out of scope (walk direction is newer-only)")

print("== v0.2.13: full resume banner still wins when newest substantive session is interrupted ==")
# If the newest substantive session is itself interrupted, the FULL banner fires (unchanged)
# and the orphaned-notes path is not consulted.
d = proj_with([("older.jsonl", CLEAN, NOW - 2 * HOUR), ("work.jsonl", WORK, NOW - 1 * HOUR)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
banner = json.loads(out).get("systemMessage", "") if out.strip() else ""
check("INTERRUPTED SESSION" in banner, "newest interrupted session -> full resume banner (orphaned path not used)")

print("\n== v0.3.0 (D11): a limit reset since the kill makes the old constraint stale ==")
import datetime as _dt
UTC = _dt.timezone.utc


def _at(y, mo, d, h, mi):
    return _dt.datetime(y, mo, d, h, mi, tzinfo=UTC)


# --- _parse_iso_utc: the transcript's own format, and a refusal to guess a zone -----------
check(detect._parse_iso_utc("2026-08-13T15:22:26.936Z") == _at(2026, 8, 13, 15, 22).replace(second=26, microsecond=936000),
      "parses the real transcript timestamp format (trailing Z)")
check(detect._parse_iso_utc("2026-08-13T15:22:26") is None,
      "a NAIVE timestamp is refused, not assumed to be UTC (would shift the day boundary)")
check(detect._parse_iso_utc("") is None and detect._parse_iso_utc(None) is None
      and detect._parse_iso_utc("garbage") is None, "empty/None/garbage -> None")

# --- _reset_period_start: the propagation minute is part of the boundary ------------------
check(detect._reset_period_start(_at(2026, 8, 14, 0, 5)) == _at(2026, 8, 13, 0, 10),
      "00:05 UTC still belongs to the PREVIOUS reset period (before the 00:10 boundary)")
check(detect._reset_period_start(_at(2026, 8, 14, 0, 15)) == _at(2026, 8, 14, 0, 10),
      "00:15 UTC belongs to the new reset period")

# --- limit_constraint_stale: period comparison, not calendar-date comparison --------------
KILLED = session([U("build it"), A("working"), A(BUDGET), LP("build it")])
# NOTE: session() does not apply its mtime argument, so stamp the file explicitly — the
# whole point of this path is that the kill time comes from the FILE when records carry none.
os.utime(KILLED, (1000, 1000))            # epoch 1000 = 1970

r = detect.limit_constraint_stale(KILLED, now=_at(2026, 8, 14, 9, 6))
check(r is not None and r["stale"] is True, "a kill many days ago -> stale (mtime fallback path)")
check(r["source"] == "mtime", "no record timestamps -> source is reported as 'mtime', not blurred")

fresh_file = session([U("build it"), A("working"), A(BUDGET)])
now_ = _dt.datetime.now(UTC)
check(detect.limit_constraint_stale(fresh_file, now=now_)["stale"] is False,
      "a file written just now -> not stale (mtime fallback agrees with the event path)")

# The case a naive date comparison gets WRONG: different calendar days, same reset period.
TS_KILL = [dict(U("build it"), timestamp="2026-08-13T23:50:00.000Z"),
           dict(A("working"), timestamp="2026-08-13T23:51:00.000Z"),
           dict(AERR(BUDGET), timestamp="2026-08-13T23:52:00.000Z")]
p = session(TS_KILL)
r = detect.limit_constraint_stale(p, now=_at(2026, 8, 14, 0, 5))
check(r["source"] == "event", "record timestamps present -> source is 'event' (real event time)")
check(r["stale"] is False,
      "kill 23:50 + resume 00:05 next CALENDAR day = same reset period -> NOT stale")
r = detect.limit_constraint_stale(p, now=_at(2026, 8, 14, 0, 15))
check(r["stale"] is True and r["boundary"] == _at(2026, 8, 14, 0, 10),
      "same kill + resume 00:15 -> stale, and names the boundary that passed")
r = detect.limit_constraint_stale(p, now=_at(2026, 8, 13, 23, 59))
check(r["stale"] is False, "resume 7 minutes after the kill -> nothing has reset")
r = detect.limit_constraint_stale(p, now=_at(2026, 8, 17, 4, 0))
check(r["stale"] is True, "resume 3 days later -> stale")
check(detect.limit_constraint_stale("/nonexistent/nope.jsonl") is None,
      "unreadable kill time -> None (question not answerable), never a guess")

# --- end to end: the banner and the model-facing context ---------------------------------
print("== v0.3.0 (D11): banner + additionalContext state the verdict ==")
d = proj_with([("k.jsonl", WORK, 1000)])   # epoch 1000 = 1970 -> unambiguously stale
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
o = json.loads(out)
banner, ctx = o.get("systemMessage", ""), o.get("hookSpecificOutput", {}).get("additionalContext", "")
check("no longer current" in banner, "stale kill -> banner says the limit is no longer current")
check("NOT in force" in ctx and "do not warn" in ctx,
      "additionalContext tells the model not to carry or warn from the expired limit")
check("UTC" in ctx, "context names the times in UTC explicitly")

d = proj_with([("k.jsonl", WORK, time.time() - 60)])   # killed a minute ago -> same period
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
o = json.loads(out)
banner, ctx = o.get("systemMessage", ""), o.get("hookSpecificOutput", {}).get("additionalContext", "")
check("no longer current" not in banner, "fresh kill -> banner does NOT claim a reset happened")
check("may still be in force" in ctx, "fresh kill -> context says the limit MAY still apply")

# The scoping test: a stall carries no constraint, so it must get no such inference at all.
d = proj_with([("s.jsonl", STALL, 1000)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
o = json.loads(out)
banner, ctx = o.get("systemMessage", ""), o.get("hookSpecificOutput", {}).get("additionalContext", "")
check("no longer current" not in banner and "reset" not in banner.lower(),
      "(S) stalled -> NO reset line in the banner (a stall carries no constraint)")
check("in force" not in ctx and "reset boundary" not in ctx,
      "(S) stalled -> NO limit-constraint language in additionalContext")

# No cap value is ever invented or implied.
d = proj_with([("k.jsonl", WORK, 1000)])
out = run([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"), "session_id": "NEW", "source": "startup"}))
whole = out
check("$" not in whole, "never prints a currency amount (no cap value is known or invented)")

# Configurability of the boundary (a provider on a different schedule).
os.environ["RESUME_INTERRUPTED_RESET_UTC_HOUR"] = "8"
os.environ["RESUME_INTERRUPTED_RESET_PROPAGATION_MIN"] = "0"
spec2 = importlib.util.spec_from_file_location("detect_cfg", SCRIPT)
detect_cfg = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(detect_cfg)
check(detect_cfg.RESET_UTC_HOUR == 8 and detect_cfg.RESET_PROPAGATION_MIN == 0,
      "reset boundary is configurable via env")
check(detect_cfg._reset_period_start(_at(2026, 8, 14, 7, 59)) == _at(2026, 8, 13, 8, 0),
      "with an 08:00 boundary, 07:59 belongs to the previous period")
r = detect_cfg.limit_constraint_stale(p, now=_at(2026, 8, 14, 7, 0))
check(r["stale"] is False,
      "08:00-boundary provider: a 23:52 kill is NOT stale at 07:00 the next calendar day")
del os.environ["RESUME_INTERRUPTED_RESET_UTC_HOUR"]
del os.environ["RESUME_INTERRUPTED_RESET_PROPAGATION_MIN"]

# ---------------------------------------------------------------------------
# v0.4.0 -- the public `interrupted` CLI (bin/interrupted + the argument surface)
#
# Two contracts are load-bearing here and each has its own test below:
#   * argv EMPTY stays SessionStart-hook mode (the hook passes no arguments);
#   * `--list` keeps working exactly as before.
# ---------------------------------------------------------------------------
print("\n== v0.4.0: bin/interrupted launcher ==")

LAUNCHER = os.path.join(HERE, "..", "bin", "interrupted")


def runx(argv, cmd=None, cwd=None, stdin=""):
    """Like run(), but keeps stderr and the exit code -- a CLI has both."""
    r = subprocess.run((cmd or [sys.executable, SCRIPT]) + argv, input=stdin,
                       capture_output=True, text=True, cwd=cwd)
    return r.stdout, r.stderr, r.returncode


def launch(argv, cwd=None, link=None):
    return runx(argv, cmd=[link or LAUNCHER], cwd=cwd)


check(os.path.exists(LAUNCHER), "bin/interrupted exists")
check(os.access(LAUNCHER, os.X_OK), "bin/interrupted is executable")
mode = subprocess.run(["git", "ls-files", "-s", "bin/interrupted"],
                      cwd=os.path.join(HERE, ".."), capture_output=True, text=True).stdout
check(mode.startswith("100755"), "bin/interrupted is mode 100755 in git, not just on disk")

d = proj_with([("work.jsonl", WORK, 1000), ("probe.jsonl", PROBE, 2000)])

# The launcher supplies --list when given no arguments, because the Python file
# defaults to hook mode and would otherwise sit waiting on stdin.
bare, _, rc_bare = launch(["--dir", d])
listed, _, rc_list = launch(["list", "--dir", d])
explicit, _, rc_expl = launch(["--list", "--dir", d])
check(rc_bare == 0 and "RECOMMENDED" in bare, "no-argument launcher lists (browse is the CLI default)")
check(bare == listed, "bare invocation == explicit `list` subcommand")
check(bare == explicit, "`--list` is a faithful compatibility alias for `list`")

# Symlink resolution: the launcher derives the plugin root from its own real path,
# so every indirection a user or an installer might introduce must still work.
tmp = tempfile.mkdtemp()
absl = os.path.join(tmp, "abs-interrupted")
os.symlink(os.path.abspath(LAUNCHER), absl)
chain = os.path.join(tmp, "chain-interrupted")
os.symlink(absl, chain)                                  # link -> link -> real file
spacedir = os.path.join(tmp, "dir with spaces")
os.mkdir(spacedir)
spaced = os.path.join(spacedir, "interrupted")
os.symlink(os.path.abspath(LAUNCHER), spaced)
reldir = os.path.join(tmp, "rel")
os.mkdir(reldir)
rel = os.path.join(reldir, "rel-interrupted")
os.symlink(os.path.join("..", "abs-interrupted"), rel)   # RELATIVE link into the chain

for label, link in (("absolute symlink", absl), ("two-deep link chain", chain),
                    ("relative symlink into that chain", rel),
                    ("path containing spaces", spaced)):
    out, err, rc = launch(["--dir", d], link=link)
    check(rc == 0 and out == bare, "resolves through a %s" % label)

# cwd must not matter once --dir/--project is explicit.
out_home, _, _ = launch(["--dir", d], cwd=os.path.expanduser("~"))
out_other, _, _ = launch(["--dir", d], cwd=tmp)
check(out_home == bare and out_other == bare,
      "same output from $HOME and from an unrelated directory (--dir wins over cwd)")

# A launcher whose Python file is missing must fail loudly, not silently print nothing.
broken_root = tempfile.mkdtemp()
os.mkdir(os.path.join(broken_root, "bin"))
broken = os.path.join(broken_root, "bin", "interrupted")
with open(LAUNCHER) as fh:
    open(broken, "w").write(fh.read())
os.chmod(broken, 0o755)
out, err, rc = launch(["--dir", d], link=broken)
check(rc != 0, "a launcher with no hooks/detect-interrupted.py exits non-zero")

print("== v0.4.0: hook mode is still what happens with NO arguments ==")
out, err, rc = runx([], stdin=json.dumps({"transcript_path": os.path.join(d, "NEW.jsonl"),
                                          "session_id": "NEW", "source": "startup"}))
check(rc == 0 and out.strip().startswith("{"),
      "empty argv + hook JSON on stdin still emits the SessionStart JSON, not CLI text")
check("systemMessage" in json.loads(out), "hook JSON still carries systemMessage")
out, _, rc = runx([], stdin="not json")
check(rc == 0 and out.strip() == "", "garbage stdin still exits 0 with no output (never blocks)")

print("== v0.4.0: --project encodes the path so the user never hand-builds one ==")
check(detect._encode_project_path("/Users/me/Work.dir") == "-Users-me-Work-dir",
      "path encoding replaces both separators and dots")
check(detect._encode_project_path("~") == detect._encode_project_path(os.path.expanduser("~")),
      "--project expands ~ before encoding")
check(detect._resolve_project_dir({"dir": "/enc/dir", "project": "/Users/me"}) == "/enc/dir",
      "--dir wins over --project (it is already encoded)")
check(detect._resolve_project_dir({"project": "/Users/me"}).endswith("-Users-me"),
      "--project is encoded and joined onto the projects root")
out, _, rc = launch(["list", "--project", "/definitely/no/such/place"])
check(rc == 0 and "No project transcript directory found" in out,
      "an unknown --project says so plainly and still exits 0")

print("== v0.4.0: recommended subcommand ==")
out, _, rc = launch(["recommended", "--dir", d])
check(rc == 0 and "RECOMMENDED" in out and out.count("\n") <= 2,
      "`recommended` prints just the one session")
check("are we back yet?" not in out, "`recommended` omits probes")
probes_only = proj_with([("p1.jsonl", PROBE, 1000), ("p2.jsonl", PROBE, 2000)])
out, _, rc = launch(["recommended", "--dir", probes_only])
check(rc == 0 and "No interrupted session with substantive work" in out,
      "`recommended` with only probes says so instead of guessing")

print("== v0.4.0: pagination is bounded by BOTH items and characters ==")
many = proj_with([("s%02d.jsonl" % i, WORK, 1000 + i) for i in range(12)])
full, _, _ = launch(["list", "--dir", many])
check(full.count("wt  ") == 12, "all 12 sessions on one page by default")
check("Showing" not in full, "no pagination footer when everything fits")
p1, _, _ = launch(["list", "--dir", many, "--limit", "5"])
check(p1.count("wt  ") == 5, "--limit 5 shows 5")
check("Showing 1-5 of 12." in p1, "footer states the window and the total")
check("--page 2" in p1, "footer gives the exact next-page command")
seen = 0
page = 1
while True:
    out, _, _ = launch(["list", "--dir", many, "--limit", "5", "--page", str(page)])
    if "past the end" in out:
        break
    seen += out.count("wt  ")
    if "Next: " not in out:
        break
    page += 1
check(seen == 12, "walking every page yields all 12 sessions -- none silently dropped")
out, _, _ = launch(["list", "--dir", many, "--limit", "5", "--page", "9"])
check("past the end" in out and "12 session" in out,
      "a page past the end says so and names the total")

# The character budget is the shell-mode ceiling, so it must bound the WHOLE output.
for mc in (400, 900, 2000):
    seen, page, over = 0, 1, False
    while True:
        out, _, _ = launch(["list", "--dir", many, "--max-chars", str(mc), "--page", str(page)])
        if "past the end" in out:
            break
        # Below the irreducible floor (header + trailer + one whole session) the
        # budget cannot be honoured; the CLI must then SAY so rather than overshoot
        # quietly, so an overshoot only counts as a failure if it went unannounced.
        if len(out) > mc and "is smaller than one session" not in out:
            over = True
        seen += out.count("wt  ")
        if "Next: " not in out:
            break
        page += 1
    check(not over, "--max-chars %d: no page overshoots the budget unannounced" % mc)
    check(seen == 12, "--max-chars %d: still reaches all 12 sessions" % mc)
out, _, _ = launch(["list", "--dir", many, "--max-chars", "1", "--page", "1"])
check(out.count("wt  ") == 1,
      "an absurdly small budget still emits one whole session, never zero (no unreachable item)")
check("is smaller than one session" in out,
      "and it says the budget could not be honoured instead of overshooting silently")
out, _, _ = launch(["list", "--dir", many, "--all"])
check(out == full, "--all removes both limits")

print("== v0.4.0: pagination cannot move the recommendation ==")
mixed = proj_with([("probe%02d.jsonl" % i, PROBE, 1000 + i) for i in range(6)]
                  + [("work.jsonl", WORK, 500)])
p1, _, _ = launch(["list", "--dir", mixed, "--limit", "2", "--page", "1"])
last, _, _ = launch(["list", "--dir", mixed, "--limit", "2", "--page", "4"])
check("RECOMMENDED" not in p1,
      "the recommendation is NOT forced onto page 1 (it is the oldest row here)")
check("RECOMMENDED" in last,
      "the recommendation stays on the page its session actually falls on")
allpages = "".join(launch(["list", "--dir", mixed, "--limit", "2", "--page", str(i)])[0]
                   for i in range(1, 5))
check(allpages.count("RECOMMENDED") == 1,
      "exactly one recommendation across all pages (computed on the full set)")

print("== v0.4.0: --json is the complete contract, never paginated ==")
out, _, rc = launch(["--dir", many, "--json"])
doc = json.loads(out)
check(rc == 0 and doc["total"] == 12 and len(doc["items"]) == 12, "--json returns every item")
out2, _, _ = launch(["list", "--dir", many, "--json", "--limit", "2"])
check(len(json.loads(out2)["items"]) == 12, "--json ignores --limit (pagination is not silent)")
row = doc["items"][0]
for k in ("path", "session_id", "time", "kind", "work_count", "reason",
          "is_downtime_note", "dangling", "queued", "recommended"):
    check(k in row, "--json row has %s" % k)
check(sum(1 for i in doc["items"] if i["recommended"]) == 1, "--json marks exactly one recommended")
out, _, _ = launch(["recommended", "--dir", probes_only, "--json"])
check(json.loads(out)["recommended"] is None,
      "`recommended --json` returns null rather than omitting the key")
out, _, _ = launch(["list", "--project", "/definitely/no/such/place", "--json"])
check(json.loads(out)["total"] == 0, "--json on a missing project is still valid JSON")

print("== v0.4.0: bad arguments fail loudly and safely ==")
for argv, why in ((["--bogus"], "unknown flag"),
                  (["list", "--page", "0"], "--page below 1"),
                  (["list", "--limit", "abc"], "non-numeric --limit"),
                  (["list", "--project"], "option with no value")):
    out, err, rc = launch(argv)
    check(rc == 2 and err.strip() != "" and out == "",
          "%s -> exit 2, message on stderr, nothing on stdout" % why)
out, err, rc = launch(["--help"])
check(rc == 0 and "Usage:" in out and "--project" in out, "--help documents the surface")
check("Read-only" in out, "--help states the read-only guarantee")

print("== v0.4.0: read-only, and pipe-safe ==")
before = sorted((f, os.path.getmtime(os.path.join(many, f))) for f in os.listdir(many))
launch(["list", "--dir", many])
launch(["--dir", many, "--json"])
after = sorted((f, os.path.getmtime(os.path.join(many, f))) for f in os.listdir(many))
check(before == after, "listing mutates no transcript (names and mtimes unchanged)")
r = subprocess.run("%s --dir %s --json | head -c 40" % (LAUNCHER, many),
                   shell=True, capture_output=True, text=True)
check(r.returncode == 0 and "BrokenPipeError" not in r.stderr and "Traceback" not in r.stderr,
      "`interrupted --json | head` closes the pipe without a traceback")




print("\n== v0.4.1: reason distinguishes a quota kill from a connection drop ==")
# Regression: classify() used to return "limit-kill" for EVERY api-error death, so a wifi/DNS
# drop told the next session a usage limit had been hit — feeding the exact budget misread the
# resume notice is supposed to prevent. Wordings below are the literal strings measured in this
# project's own transcripts (118 quota kills, all apiErrorStatus=429; every transport death
# carrying no status at all), so these are ground truth rather than invented signatures.
def AS_(t, status=None):
    """Assistant error record with an optional apiErrorStatus, as the client writes it."""
    r = {"type": "assistant", "isApiErrorMessage": True,
         "message": {"role": "assistant", "content": [{"type": "text", "text": t}]}}
    if status is not None:
        r["apiErrorStatus"] = status
    return r

DROP = "API Error: Unable to connect to API (ENOTFOUND)"
DNS = "API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)"
REFUSED = "API Error: Connection refused — a firewall or proxy may be blocking it"
RESET = "API Error: Unable to connect to API (ECONNRESET)"
STALL = "API Error: Response stalled mid-stream. The response above may be incomplete."
BUSY = "API Error: 503 Server is busy (max concurrent requests reached). Retrying…"
KILL429 = "API Error: Request rejected (429) · Budget has been exceeded! Key=Joyia"

for label, rec in (("ENOTFOUND", AS_(DROP)), ("DNS wording", AS_(DNS)),
                   ("connection refused", AS_(REFUSED)), ("ECONNRESET", AS_(RESET)),
                   ("stalled stream", AS_(STALL))):
    c = detect.classify(session([U("go"), A("working"), rec, LP("go")]))
    check(c["interrupted"] and c["reason"] == "connection-drop",
          "%s is a connection-drop, NOT a limit-kill" % label)

c = detect.classify(session([U("go"), A("working"), AS_(KILL429, 429), LP("go")]))
check(c["interrupted"] and c["reason"] == "limit-kill", "a real 429 budget refusal is a limit-kill")
c = detect.classify(session([U("go"), A("working"), AS_(BUSY, 503), LP("go")]))
check(c["interrupted"] and c["reason"] == "api-error", "a 503 busy-server refusal is an api-error")
# Status wins over wording: a 429 is a quota refusal even if the text mentions the network.
c = detect.classify(session([U("go"), A("working"), AS_("rate limited on the network edge", 429), LP("go")]))
check(c["reason"] == "limit-kill", "apiErrorStatus=429 outranks transport wording")
# Legacy transcripts predate the status field; a budget kill there must still be a limit-kill.
c = detect.classify(session([U("go"), A("working"), A(BUDGET), LP("go")]))
check(c["reason"] == "limit-kill", "legacy budget kill with NO status is still a limit-kill")

print("\n== v0.4.1: the reset inference stays scoped to a real quota kill ==")
# limit_constraint_stale answers "has a reset boundary passed" and is meaningful ONLY for a
# quota kill. A connection drop used no quota, so the banner must not claim a limit reset.
old = time.time() - 3 * 86400
def _aged(recs):
    p = session(recs); os.utime(p, (old, old)); return p

def _banner(recs):
    """Everything _emit_auto emits, as one string. It PRINTS the hook JSON rather than
    returning it, so a returned value is None and every `not in` assertion against it would
    pass vacuously — capture stdout instead."""
    p = _aged(recs)
    buf = io.StringIO()
    real = sys.stdout
    sys.stdout = buf
    try:
        detect._emit_auto(p, detect.classify(p), 0)
    finally:
        sys.stdout = real
    return buf.getvalue()

blob = _banner([U("do the thing"), A("started"), AS_(DROP), LP("do the thing")])
check(blob.strip() != "" and "null" not in blob[:6], "the banner capture actually captured output")
check("lost its connection" in blob, "connection-drop banner names the connection, not a limit")
check("limit reset has passed" not in blob, "connection-drop banner claims NO limit reset")
check("usage/limit error" not in blob, "connection-drop context does not assert a usage limit")
check("a lost connection to the API" in blob, "connection-drop context names the real cause")

blob429 = _banner([U("do the thing"), A("started"), AS_(KILL429, 429), LP("do the thing")])
check("usage/API limit" in blob429, "limit-kill banner still names the limit")
check("limit reset has passed" in blob429, "limit-kill 3 days old still reports the passed reset")


print("\n== v0.6.0 (batch F): liveness — a LIVE session is never a resume candidate ==")
# The registry records live sessions as <pid>.json with sessionId + startedAt (epoch ms).
# A session is LIVE only on positive evidence: a registry entry whose pid is alive AND whose
# start time matches (a recycled pid is a different process wearing the same number).
REG = tempfile.mkdtemp()
os.environ["RESUME_INTERRUPTED_SESSIONS_DIR"] = REG
MY_PID = os.getpid()
NOW_MS = time.time() * 1000


def reg_entry(name, rec):
    with open(os.path.join(REG, name), "w") as fh:
        json.dump(rec, fh)


def clear_reg():
    for f in os.listdir(REG):
        os.unlink(os.path.join(REG, f))


reg_entry("me.json", {"pid": MY_PID, "sessionId": "sid-live", "startedAt": NOW_MS})
check(detect.session_live("sid-live", REG) is True, "live: registry entry, pid alive, start matches")

# Without the registry_dir argument the env override is honoured.
check(detect.session_live("sid-live") is True, "env RESUME_INTERRUPTED_SESSIONS_DIR is honoured")
check(detect.session_live("sid-unknown") is False, "no matching entry -> not live (never by default)")
check(detect.session_live("") is False, "empty session id -> not live, never raises")

reg_entry("dead.json", {"pid": 999999, "sessionId": "sid-dead", "startedAt": NOW_MS})
check(detect.session_live("sid-dead", REG) is False, "dead pid -> not live (no os.kill match)")

reg_entry("recycled.json", {"pid": MY_PID, "sessionId": "sid-recycled", "startedAt": NOW_MS - 3 * 3600 * 1000})
check(detect.session_live("sid-recycled", REG) is False,
      "pid-reuse guard: alive pid whose start time does NOT match the record -> not live")

reg_entry("nostart.json", {"pid": MY_PID, "sessionId": "sid-nostart"})
check(detect.session_live("sid-nostart", REG) is True,
      "no startedAt recorded -> fails open (alive pid is enough; never read a live session as dead)")

reg_entry("corrupt.json", None)  # unparseable
with open(os.path.join(REG, "corrupt.json"), "w") as fh:
    fh.write("{not json")
check(detect.session_live("sid-live", REG) is True, "corrupt registry entry is skipped, others still consulted")
clear_reg()
check(detect.session_live("sid-live", REG) is False, "empty registry -> not live")

print("== v0.6.0: the env override fails loudly when set but unusable ==")
code = ("import importlib.util,os,sys;spec=importlib.util.spec_from_file_location("
        "'d',%r);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);" % SCRIPT) + \
       "os.environ['RESUME_INTERRUPTED_SESSIONS_DIR']='/definitely/no/such/dir';print(m.session_live('x'))"
r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
check("RESUME_INTERRUPTED_SESSIONS_DIR" in r.stderr and "no such" in r.stderr.lower() or "not a directory" in r.stderr,
      "an override that is set but invalid names itself on stderr (never a silent fallthrough)")
check(r.stdout.strip() == "False", "and liveness answers False on no positive evidence, not by reading the default")
os.environ["RESUME_INTERRUPTED_SESSIONS_DIR"] = REG

print("== v0.6.0: live sessions are excluded from the listing and the recommendation ==")
d = proj_with([("work.jsonl", WORK, NOW - 3 * HOUR)])
reg_entry("other.json", {"pid": MY_PID, "sessionId": "work", "startedAt": time.time() * 1000})
out, err, rc = launch(["list", "--dir", d])
check(out.strip() == "" or "work" not in out, "a LIVE work session is not listed as interrupted")
out, _, _ = launch(["recommended", "--dir", d])
check("No interrupted session" in out, "a LIVE session is not recommended for resume")
# Same transcript, dead pid -> listed again (the registry decides, nothing else changed).
clear_reg()
reg_entry("dead.json", {"pid": 999999, "sessionId": "work", "startedAt": NOW_MS})
out, _, _ = launch(["list", "--dir", d])
check("RECOMMENDED" in out, "the same session with a dead pid is listed again")

print("== v0.6.0: registry absent -> a freshly written session says 'may still be running' ==")
# The registry "cannot answer" is simulated with an override to a nonexistent dir (the same
# code branch as an absent default registry) — the real default exists on this machine.
os.environ["RESUME_INTERRUPTED_SESSIONS_DIR"] = "/definitely/no/such/dir"
d = proj_with([("fresh.jsonl", WORK, time.time() - 30)])
out, err, rc = launch(["list", "--dir", d])
check("may still be running" in out, "fresh write + no registry -> 'may still be running', never a confident stalled verdict")
out, _, _ = launch(["recommended", "--dir", d])
check("No interrupted session" in out, "a may-still-be-running session is not recommended either")
d = proj_with([("old.jsonl", WORK, time.time() - 2 * HOUR)])
out, _, _ = launch(["list", "--dir", d])
check("may still be running" not in out, "an old write with no registry gets no such hedge")

print("== v0.6.0 (batch F): the mid-tool kill shape — (M) ==")
TOOL = {"type": "assistant", "message": {"role": "assistant", "content": [
    {"type": "text", "text": "running it"},
    {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}]}}
TOOL_RESULT = {"type": "user", "message": {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "tu1", "content": "out"}]}}
c = detect.classify(session([U("go"), A("working"), TOOL]))
check(c["interrupted"] and c["reason"] == "mid-tool",
      "(M) a persisted tool_use with no tool_result after it = killed mid-tool (neither E nor S)")
c = detect.classify(session([U("go"), A("working"), TOOL, TOOL_RESULT]))
check(not c["interrupted"], "the result arriving completes the turn -> not mid-tool")
c = detect.classify(session([U("go"), A("working"), TOOL, TOOL_RESULT,
                             {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "next"}]}}]))
check(c["interrupted"] and c["reason"] == "stalled", "a real prompt after a completed tool call is a stall, not mid-tool")
# (E) outranks (M): an error turn is the stronger, constraint-carrying verdict.
c = detect.classify(session([U("go"), A("working"), TOOL, AERR(BUDGET)]))
check(c["reason"] == "limit-kill", "(E) an error turn after the tool_use still wins over mid-tool")
# A text-only session is unaffected.
c = detect.classify(session([U("go"), A("done"), LP("go")]))
check(not c["interrupted"], "text-only clean session is not mid-tool")
# The banner names the shape and carries no limit constraint.
p = _aged([U("do the thing"), A("started"), TOOL])
blob = _banner([U("do the thing"), A("started"), TOOL])
check("tool call" in blob and "never returned its result" in blob,
      "mid-tool banner names the shape (killed between the call and its result)")
check("limit" not in blob.lower(), "mid-tool carries no limit constraint (no reset language)")

print("== v0.8.0: the between-turns death shape — (T) ==")
# Measured 2026-10-29 (cc-setup-54): the final human turn was fully answered, the session
# issued the NEXT api-request, and died before any reply arrived — tail is
# tool_result -> attachment -> api-request-blob -> api-request. Every existing shape checks a
# record against its completion (error turn / tool_use / unanswered prompt); this death lands
# in the gap BETWEEN turns, so 0.7.0 read the transcript as "not interrupted" and a
# half-finished request sat unnoticed for ~22 hours.
APIREQ = {"type": "api-request", "timestamp": "2026-10-29T17:12:13.396Z"}
APIREQ_BLOB = {"type": "api-request-blob"}
ATTACH = {"type": "attachment"}
BT = [U("make a waypoint and a report"), A("Two things to build: the waypoint and the report."),
      TOOL, TOOL_RESULT, ATTACH, APIREQ_BLOB, APIREQ]
c = detect.classify(session(BT))
check(c["interrupted"] and c["reason"] == "between-turns",
      "(T) trailing api-request after an answered turn = killed between turns")
c = detect.classify(session(BT + [A("done — both halves")]))
check(not c["interrupted"], "(T) a reply after the in-flight request means it completed — healthy")
# (E) outranks (T), same as it outranks (M): an error turn is the stronger, constraint-carrying verdict.
c = detect.classify(session([U("go"), A("working"), APIREQ, AERR(BUDGET)]))
check(c["reason"] == "limit-kill", "(E) an error turn after the api-request still wins over between-turns")
# An UNANSWERED prompt followed by its own in-flight request is a stall — the request is just
# the mechanism of the stall, and the dangling-prompt verdict is the useful one.
c = detect.classify(session([U("go"), A("working"), TOOL, TOOL_RESULT, U("next"), APIREQ]))
check(c["reason"] == "stalled", "an unanswered prompt followed by its in-flight request is a stall, not between-turns")
# The tail walk skips attachment records, and must not fire on one alone.
c = detect.classify(session([U("go"), A("done"), LP("go"), ATTACH]))
check(not c["interrupted"], "a trailing attachment record alone is not a death")

print("== v0.6.0: the harvest also reads queue-operation records (prompts that never became turns) ==")
# Real shape (session 52a9a5bf, 2026-10-25 15:11 UTC): a user message typed mid-turn was
# absorbed into the running context and recorded ONLY as a queue-operation record — never a
# user turn — so a harvest that reads user roles alone loses it entirely.
QOP = {"type": "queue-operation", "operation": "remove", "reason": "absorbed_mid_turn",
       "content": "Heads-up: the plan changed under you. Re-read the C0 amendment block before batch C."}
Q = [U("build the thing"), A("starting"), QOP, LP("build the thing")]
harvested = detect.queued_prompts(Q)
check(any("C0 amendment" in h for h in harvested),
      "a queue-operation record's content is harvested (it never became a user turn)")
# Dedup: a queued message that ALSO arrived as a user turn must not be surfaced twice.
QD = [U("build the thing"), A("starting"), QOP,
      {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "Heads-up: the plan changed under you. Re-read the C0 amendment block before batch C."}]}},
      LP("build the thing")]
check(detect.queued_prompts(QD).count("Heads-up: the plan changed under you. Re-read the C0 amendment block before batch C.") == 1,
      "a queue record whose text also exists as a user turn is not duplicated")
check(detect.queued_prompts([U("hi"), A("hi"), U("bye"), A("cya")]) == [],
      "clean session: queue-record harvest adds nothing")

print("== v0.6.0: mutation — each new mechanism is what fires ==")
reg_entry("other.json", {"pid": MY_PID, "sessionId": "work", "startedAt": time.time() * 1000})
d = proj_with([("work.jsonl", WORK, NOW - 3 * HOUR)])
real_live = detect.session_live
try:
    detect.session_live = lambda sid, rd=None: False
    out, _, _ = launch(["list", "--dir", d])
finally:
    detect.session_live = real_live
check("RECOMMENDED" in out, "mutation: without the liveness check the live session IS listed (mechanism proven)")
real_um = detect._unmatched_tool_use
try:
    detect._unmatched_tool_use = lambda recs: False
    c = detect.classify(session([U("go"), A("working"), TOOL]))
finally:
    detect._unmatched_tool_use = real_um
check(not c["interrupted"] and c["reason"] != "mid-tool",
      "mutation: without the mid-tool check the shape reads as clean (mechanism proven)")
os.environ["RESUME_INTERRUPTED_SESSIONS_DIR"] = REG

print("== v0.7.0: `interrupted tail` -- the read-only transcript tail parser ==")
TOOL_BAD = {"type": "user", "message": {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "tu1", "is_error": True, "content": "boom: exit 2"}]}}
TAIL_RECS = [U("build the thing"), A("starting on it"), TOOL, TOOL_RESULT, A("step one done"),
             QOP, U("and also check the docs"), LP("and also check the docs")]
tp = session(TAIL_RECS)
before = open(tp, "rb").read()
out, err, code = launch(["tail", tp])
check(code == 0, "tail exits 0 on a readable transcript (stderr: %s)" % err.strip()[:80])
check("starting on it" in out and "step one done" in out, "tail shows the assistant text turns")
check("tool_use Bash" in out and "tool_result" in out, "tail condenses tool calls and their results")
check("and also check the docs" in out, "tail shows the unanswered user turn")
check("queue-operation" in out and "C0 amendment" in out,
      "tail harvests the queue-operation record and says it never became a user turn")
check(open(tp, "rb").read() == before, "tail is read-only: the transcript is byte-identical")

out, _, _ = launch(["tail", tp, "--records", "2"])
check("starting on it" not in out and "and also check the docs" in out,
      "--records N limits the condensed tail to the last N conversational records")

out, _, _ = launch(["tail", session([U("go"), A("working"), TOOL_BAD]), "--no-audit"])
check("ERROR" in out and "boom: exit 2" in out, "an errored tool_result is flagged as an error, with its text")

mt = session([U("go"), A("working"), TOOL])
out, _, _ = launch(["tail", mt, "--no-audit"])
check("tu1" in out and "unanswered tool_use" in out.lower(),
      "a tool_use with no result is listed as unanswered by id and name")
check("mid-tool" in out, "tail states the detector's classification for the session")
check("reset" in out.lower() and "quota" in out.lower(),
      "even a non-(E) death gets the reset-boundary check, labelled as relevant only for a quota kill")

out, _, _ = launch(["tail", mt, "--json", "--no-audit"])
try:
    tj = json.loads(out)
except Exception:
    tj = {}
check(tj.get("reason") == "mid-tool" and tj.get("unanswered_tool_use") and "records" in tj,
      "--json is one parseable object with reason, unanswered_tool_use and records")

_, err, code = launch(["tail"])
check(code == 2 and "tail" in err, "tail with no target is a loud usage error, exit 2")
_, err, code = launch(["tail", tp, "--bogus"])
check(code == 2 and "--bogus" in err, "an unknown tail flag is a loud error, exit 2")
_, err, code = launch(["tail", os.path.join(tempfile.mkdtemp(), "nope.jsonl")])
check(code != 0 and "nope.jsonl" in err, "a missing transcript fails loudly and names it")
out, _, _ = launch(["--help"])
check("tail" in out and "--records" in out, "--help documents the tail subcommand and its flags")

# A session id resolves inside the project dir without a full path.
pd = proj_with([("abc12345-aaaa.jsonl", TAIL_RECS, None)])
out, _, code = launch(["tail", "abc12345", "--dir", pd, "--no-audit"])
check(code == 0 and "step one done" in out, "tail resolves a session-id prefix inside --dir")

print("== v0.7.0: precedence -- an error turn outranks an unmatched tool_use ==")
c = detect.classify(session([U("go"), A("working"), TOOL, AERR(BUDGET)]))
check(c["reason"] == "limit-kill",
      "(E) outranks (M) when the error turn IS persisted (pins the documented precedence)")
c = detect.classify(session([U("go"), A("working"), TOOL, AERR("API Error: Connection error."),
                             LP("go")]))
check(c["reason"] != "mid-tool", "a connection-drop error after the tool_use is never relabelled mid-tool")

print("== v0.7.0: soft dependency on audit-loose-ends ==")
FAKE_AUDIT = ("#!/usr/bin/env python3\nimport sys\n"
              "print('FAKE-AUDIT-DIGEST args=' + ' '.join(sys.argv[1:]))\n")


def fake_plugin(root, version):
    """A cache dir for audit-loose-ends at `version` under root, with a fake scanner."""
    sd = os.path.join(root, "cache", "mkt", "audit-loose-ends", version, "scripts")
    os.makedirs(sd)
    with open(os.path.join(sd, "audit-scan.py"), "w") as fh:
        fh.write(FAKE_AUDIT.replace("FAKE-AUDIT-DIGEST", "FAKE-AUDIT-DIGEST v%s" % version))
    return os.path.dirname(sd)


def tail_env(extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith("RESUME_INTERRUPTED_AUDIT")}
    e.update(extra)
    return e


def tail_with_env(args, extra):
    r = subprocess.run([LAUNCHER] + args, capture_output=True, text=True, env=tail_env(extra))
    return r.stdout, r.stderr, r.returncode


# Absent: nothing installed -> a SILENT skip, never an error, never a section.
empty_root = tempfile.mkdtemp()
out, err, code = tail_with_env(["tail", tp], {"RESUME_INTERRUPTED_PLUGINS_DIR": empty_root})
check(code == 0 and "FAKE-AUDIT" not in out and "audit" not in err.lower(),
      "audit-loose-ends absent: tail skips the audit silently (exit 0, no error)")

# Present via the cache: the HIGHEST numeric version wins (0.13.1 beats 0.9.0 -- lexically it would not).
root = tempfile.mkdtemp()
for _v in ("0.9.0", "0.13.1", "0.10.0"):
    fake_plugin(root, _v)
out, _, code = tail_with_env(["tail", tp], {"RESUME_INTERRUPTED_PLUGINS_DIR": root})
check(code == 0 and "FAKE-AUDIT-DIGEST v0.13.1" in out,
      "audit-loose-ends present: its scanner runs, the highest NUMERIC cache version wins")
check(("args=%s" % tp) in out,
      "the scanner is given the explicit transcript PATH, never --last (the mtime trap)")

# Present via the registry: installPath is preferred over the cache sort.
root2 = tempfile.mkdtemp()
fake_plugin(root2, "0.13.1")
pinned = fake_plugin(root2, "0.5.0")
with open(os.path.join(root2, "installed_plugins.json"), "w") as fh:
    json.dump({"version": 2, "plugins": {"audit-loose-ends@mkt": [{"installPath": pinned}]}}, fh)
out, _, _ = tail_with_env(["tail", tp], {"RESUME_INTERRUPTED_PLUGINS_DIR": root2})
check("FAKE-AUDIT-DIGEST v0.5.0" in out,
      "the registry's installPath outranks the highest cache dir")

out, _, _ = tail_with_env(["tail", tp, "--no-audit"], {"RESUME_INTERRUPTED_PLUGINS_DIR": root})
check("FAKE-AUDIT" not in out, "--no-audit suppresses the audit section")

# An explicit override that is SET but unusable must fail loudly, not fall through.
_, err, code = tail_with_env(["tail", tp], {"RESUME_INTERRUPTED_AUDIT_SCAN": "/no/such/audit-scan.py",
                                            "RESUME_INTERRUPTED_PLUGINS_DIR": root})
check(code != 0 and "RESUME_INTERRUPTED_AUDIT_SCAN" in err,
      "RESUME_INTERRUPTED_AUDIT_SCAN set but missing aborts and names the variable")
override = os.path.join(tempfile.mkdtemp(), "audit-scan.py")
with open(override, "w") as fh:
    fh.write(FAKE_AUDIT.replace("FAKE-AUDIT-DIGEST", "FAKE-AUDIT-DIGEST override"))
out, _, code = tail_with_env(["tail", tp], {"RESUME_INTERRUPTED_AUDIT_SCAN": override,
                                             "RESUME_INTERRUPTED_PLUGINS_DIR": root})
check(code == 0 and "FAKE-AUDIT-DIGEST override" in out, "a valid override wins over discovery")

# A scanner that crashes must not take the tail down with it.
bad = os.path.join(tempfile.mkdtemp(), "audit-scan.py")
with open(bad, "w") as fh:
    fh.write("#!/usr/bin/env python3\nimport sys\nsys.stderr.write('kaboom\\n')\nsys.exit(3)\n")
out, _, code = tail_with_env(["tail", tp], {"RESUME_INTERRUPTED_AUDIT_SCAN": bad})
check(code == 0 and "step one done" in out and "audit" in out.lower() and "3" in out,
      "a failing scanner is reported in the audit section; the tail itself still succeeds")

print("== v0.8.0: --all-projects — sessions from every project folder ==")
ROOT_SAVE = os.environ.get("RESUME_INTERRUPTED_PROJECTS_ROOT")
ROOT = tempfile.mkdtemp()
os.environ["RESUME_INTERRUPTED_PROJECTS_ROOT"] = ROOT
try:
    pA = os.path.join(ROOT, "-Users-me-projA")
    os.makedirs(pA)
    pB = os.path.join(ROOT, "-Users-me-projB")
    os.makedirs(pB)
    old = time.time() - 2 * HOUR
    fA = os.path.join(pA, "a.jsonl")
    with open(fA, "w") as fh:
        for r in WORK:
            fh.write(json.dumps(r) + "\n")
    os.utime(fA, (old, old))
    fB = os.path.join(pB, "b.jsonl")
    with open(fB, "w") as fh:
        for r in PROBE:
            fh.write(json.dumps(r) + "\n")
    os.utime(fB, (old, old))
    out, _, rc = launch(["list", "--all-projects"])
    check(rc == 0 and "-Users-me-projA" in out and "-Users-me-projB" in out,
          "--all-projects lists sessions from BOTH project dirs, each labelled with its project")
    check("RECOMMENDED" in out, "the recommendation still marks the substantive session across projects")
    out2, _, _ = launch(["list", "--dir", pA])
    check("-Users-me-projB" not in out2, "without the flag, other projects stay invisible")
    # Subagent transcripts never enter the listing (depth-1 scan; explicit guard as defense).
    sub = os.path.join(pB, "sidX", "subagents")
    os.makedirs(sub)
    with open(os.path.join(sub, "sub.jsonl"), "w") as fh:
        for r in WORK:
            fh.write(json.dumps(r) + "\n")
    out3, _, _ = launch(["list", "--all-projects"])
    check("sub.jsonl" not in out3, "a subagent transcript is not listed as a session")
    # A LIVE session is excluded, whatever its tail ("sid-live" is registered live by the v0.6 block).
    fLive = os.path.join(pB, "sid-live.jsonl")
    with open(fLive, "w") as fh:
        for r in WORK:
            fh.write(json.dumps(r) + "\n")
    os.utime(fLive, (old, old))
    out4, _, _ = launch(["list", "--all-projects"])
    check("sid-live" not in out4, "a live session (registry + alive pid) is not listed cross-project")
    # A corrupt sidecar title must not break the listing.
    os.makedirs(os.path.join(pA, "a"))
    with open(os.path.join(pA, "a", "custom-title.json"), "w") as fh:
        fh.write("{not json")
    out5, _, rc5 = launch(["list", "--all-projects"])
    check(rc5 == 0 and "-Users-me-projA" in out5, "a corrupt custom-title.json never breaks the listing")
    # An invalid override fails loudly, naming the variable (never a silent fallback).
    os.environ["RESUME_INTERRUPTED_PROJECTS_ROOT"] = os.path.join(ROOT, "nope")
    outE, errE, rcE = launch(["list", "--all-projects"])
    check(rcE != 0 and "RESUME_INTERRUPTED_PROJECTS_ROOT" in (outE + errE),
          "an invalid projects-root override aborts loudly, naming the variable")
finally:
    if ROOT_SAVE is None:
        os.environ.pop("RESUME_INTERRUPTED_PROJECTS_ROOT", None)
    else:
        os.environ["RESUME_INTERRUPTED_PROJECTS_ROOT"] = ROOT_SAVE

print("== v0.8.0: session titles from the custom-title sidecar ==")
p = session([U("hi"), A("ho")])
check(detect._session_title(p) == "", "no sidecar -> empty title, never an error")
os.makedirs(os.path.join(os.path.dirname(p), "s"))
tj = os.path.join(os.path.dirname(p), "s", "custom-title.json")
with open(tj, "w") as fh:
    json.dump({"customTitle": "📡 something"}, fh)
check(detect._session_title(p) == "📡 something", "the custom title is read from the sidecar")
with open(tj, "w") as fh:
    fh.write("{corrupt")
check(detect._session_title(p) == "", "a corrupt sidecar degrades to an empty title")
check(detect._session_title("/nonexistent/x.jsonl") == "", "a nonexistent transcript degrades to an empty title")

print("== v0.8.0 review fixes: guards, bounds, scopes ==")
os.environ["RESUME_INTERRUPTED_PROJECTS_ROOT"] = ROOT
try:
    # A subagent transcript SYMLINKED to project level must be excluded — the depth-1 glob
    # alone does not see through a symlink placed at the project level (review finding).
    os.symlink(os.path.join(pB, "sidX", "subagents", "sub.jsonl"), os.path.join(pA, "flat-sub.jsonl"))
    out6j, _, _ = launch(["list", "--all-projects", "--json"])
    paths6 = [it["path"] for it in json.loads(out6j)["items"]]
    check(not any(en.endswith("flat-sub.jsonl") for en in paths6),
          "a subagent transcript symlinked to project level is excluded")
    # The cross-project scan is bounded by a recency window: classify() parses whole
    # transcripts, so an ancient store must not make --all-projects hang (review finding).
    ancient = os.path.join(pB, "old.jsonl")
    with open(ancient, "w") as fh:
        for r in WORK:
            fh.write(json.dumps(r) + "\n")
    old40 = time.time() - 40 * 24 * HOUR
    os.utime(ancient, (old40, old40))
    out7j, _, rc7 = launch(["list", "--all-projects", "--json"])
    paths7 = [it["path"] for it in json.loads(out7j)["items"]]
    check(rc7 == 0 and not any(en.endswith("old.jsonl") for en in paths7),
          "a transcript older than the recency window is skipped cross-project")
    check(any(en.endswith("/a.jsonl") for en in paths7), "recent sessions are still listed")
    WSAVE = os.environ.get("RESUME_INTERRUPTED_ALL_PROJECTS_WINDOW_S")
    try:
        os.environ["RESUME_INTERRUPTED_ALL_PROJECTS_WINDOW_S"] = "0"
        out8j, _, _ = launch(["list", "--all-projects", "--json"])
        paths8 = [it["path"] for it in json.loads(out8j)["items"]]
        check(any(en.endswith("old.jsonl") for en in paths8),
              "window 0 disables the bound (the ancient transcript returns)")
        os.environ["RESUME_INTERRUPTED_ALL_PROJECTS_WINDOW_S"] = "bogus"
        outB, errB, rcB = launch(["list", "--all-projects"])
        check(rcB != 0 and "RESUME_INTERRUPTED_ALL_PROJECTS_WINDOW_S" in (outB + errB),
              "an invalid window override aborts loudly, naming the variable")
    finally:
        if WSAVE is None:
            os.environ.pop("RESUME_INTERRUPTED_ALL_PROJECTS_WINDOW_S", None)
        else:
            os.environ["RESUME_INTERRUPTED_ALL_PROJECTS_WINDOW_S"] = WSAVE
    # recommended's no-pick message names its scope under --all-projects (review finding).
    ROOT2 = tempfile.mkdtemp()
    os.environ["RESUME_INTERRUPTED_PROJECTS_ROOT"] = ROOT2
    pC = os.path.join(ROOT2, "-Users-me-projC")
    os.makedirs(pC)
    fC = os.path.join(pC, "c.jsonl")
    with open(fC, "w") as fh:
        for r in CLEAN:
            fh.write(json.dumps(r) + "\n")
    os.utime(fC, (time.time() - HOUR, time.time() - HOUR))
    out9, _, rc9 = launch(["recommended", "--all-projects"])
    check(rc9 == 0 and "all project folders" in out9,
          "recommended --all-projects with no pick names its scope")
finally:
    if ROOT_SAVE is None:
        os.environ.pop("RESUME_INTERRUPTED_PROJECTS_ROOT", None)
    else:
        os.environ["RESUME_INTERRUPTED_PROJECTS_ROOT"] = ROOT_SAVE

# A title holding bytes invalid in the platform default encoding still surfaces — utf-8 with
# errors=replace, not a silent loss (review finding: LANG=C lost emoji titles to ValueError).
p = session([U("hi"), A("ho")])
os.makedirs(os.path.join(os.path.dirname(p), "s"), exist_ok=True)
tj = os.path.join(os.path.dirname(p), "s", "custom-title.json")
with open(tj, "wb") as fh:
    fh.write(b'{"customTitle": "bad \xff\xfe bytes"}')
t = detect._session_title(p)
check(t != "" and "bad" in t, "a title with invalid-utf8 bytes still surfaces (replacement, not loss)")

print("== branding: the recovery-offer context leads with the ⚡️ mark (plan W3 Task 11) ==")
# The offer is a credit moment (the plugin just recovered a lost session); the all-clean note
# is an idle surface and must stay unbranded. The user-visible box already carried the ⚡️
# header upstream (line ~890, with the VS16 note); this pins the model-facing context string.
_hooksrc = open(detect.__file__).read()
check("⚡️ resume-interrupted: your most recent substantive session" in _hooksrc,
      "the offer context leads with '⚡️ resume-interrupted:'")
check('ctx = ("resume-interrupted: your most recent substantive session' not in _hooksrc,
      "the unbranded offer prefix is gone")  # lowercase 'substantive' = the offer; the all-clean
# note uses uppercase SUBSTANTIVE and must keep matching its own unbranded line.
check('ctx = ("resume-interrupted: your most recent SUBSTANTIVE session was clean' in _hooksrc,
      "the all-clean note stays unbranded (idle surface)")

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)