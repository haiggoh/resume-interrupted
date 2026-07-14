#!/usr/bin/env python3
"""resume-interrupted — interrupted-session detector.

Two modes:

  (default, SessionStart hook)  Reads the hook JSON on stdin, looks at the MOST RECENT
      *substantive* prior session in the same project, and if it was cut off mid-task
      emits a user-visible banner (systemMessage) + a model-facing notice
      (hookSpecificOutput.additionalContext) so Claude can offer to resume.

  --list [--dir DIR]            Prints ALL interrupted sessions in the project (most
      recent first), probes included, with the most likely resume candidate marked.
      For the on-demand "show me everything I haven't picked back up" flow.

Why: a session that dies on a usage/credit limit, a crash, or a dropped connection can't
record afterward that its work was unfinished. The only trace is the transcript.

Design guarantees: never blocks a session (any error -> print nothing, exit 0); stdlib
only; reads transcripts, writes nothing.

Detection — a session was interrupted if EITHER:
  (E) LIMIT KILL  the last assistant turn is an API/budget error — recognised by the
                  transcript's own `isApiErrorMessage` marker, or (fallback) an error
                  signature at the START of the turn. A turn that merely *discusses* an
                  error phrase mid-text is NOT a kill.
  (S) STALLED     the final human input (last user record, or an orphaned last-prompt)
                  never received an assistant reply.

De-noising: the auto banner considers only the most recent *substantive* session (>=1
real assistant turn). Bare "are we back yet?" probes are skipped for the recommendation,
so the offer re-appears after a killed/empty session but goes quiet once a clean
substantive session exists (i.e. you've moved on). --list still shows probes, for
transparency, since a "probe" is occasionally a real request typed on a dead connection.
"""

import sys, os, json, glob

ERROR_SIGNATURES = ("Budget has been exceeded", "API Error: Request rejected", "usage limit reached")


def _records(path):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def _human_text(msg):
    c = msg.get("content")
    if isinstance(c, str):
        t = c
    elif isinstance(c, list):
        t = ""
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_result":
                return None
            if b.get("type") == "text":
                t = b.get("text", "")
                break
    else:
        return None
    if not t or t.lstrip().startswith("<"):
        return None
    return t.strip()


def _assistant_text(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    t = ""
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                t += b.get("text", "")
    return t


def _norm(s):
    return " ".join((s or "").split())


def _quote(s, n=100):
    """Trim a dangling prompt for the banner: cap length (~100 chars keeps the banner to
    about one line while giving enough of the prompt to recognise the thread), cut on a
    WORD boundary, and mark
    truncation with a single '…'. A mid-word cut ("…from a human persp") reads as if the
    text itself were severed — a false signal in a tool whose job is flagging severed
    sessions. Claude Code's own last-prompt marker may already end in '…'; normalise so we
    never double it and never lose the truncation cue."""
    s = _norm(s)
    upstream_cut = s.endswith("…")
    core = s[:-1].rstrip() if upstream_cut else s
    if len(core) <= n:
        return core + "…" if upstream_cut else core
    cut = core[:n].rstrip()
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return cut + "…"


def _is_error_turn(rec, text):
    """True if this assistant record is the API/limit error the session died on.

    Trust the transcript's own structural marker first (`isApiErrorMessage`, set by the
    client on real error turns). Fall back to an ANCHORED text match — an error signature
    at the START of the turn — so a healthy turn that merely *quotes* an error phrase
    mid-paragraph (e.g. documenting how to recognise a budget kill) is not mistaken for
    one. An unanchored `sig in text` match conflates "died on" with "wrote about".
    """
    # Structural marker first — wording-agnostic. The client tags real error turns with
    # isApiErrorMessage / apiErrorStatus regardless of the message text, so this keeps
    # working when the error wording changes (e.g. the budget cap message) and covers
    # error kinds we never enumerated (overloaded, rate-limit, server error).
    if rec.get("isApiErrorMessage") or rec.get("apiErrorStatus"):
        return True
    # Legacy fallback for transcripts/harnesses lacking the marker: an error signature at
    # the START of the turn (anchored — a turn that merely discusses the phrase is not a kill).
    return _norm(text).startswith(ERROR_SIGNATURES)


# Availability-probe noise the user types into a dead connection ("are we back yet?"). We
# drop these when harvesting queued notes so real queued work isn't buried. Conservative:
# only a SHORT message that is (or clearly contains) an availability check is dropped — a
# real note is never sacrificed to over-eager filtering.
_PROBE_EXACT = {
    "", "?", "hi", "hey", "hello", "yo", "test", "ping", "u there", "you there",
    "anyone", "anyone there", "alive", "still there", "back", "back yet", "we back",
    "you back", "are we back", "are we back yet", "ready", "working", "working now",
    "still blocked", "still down", "still stuck", "you up", "up yet", "you alive",
}
_PROBE_KEYWORDS = (
    "back yet", "are we back", "you there", "still there", "still blocked", "still down",
    "still stuck", "working now", "you up ", "back online", "are you there",
    "budget back", "unblocked yet", "you alive", "back yet",
)


def _is_probe_text(t):
    """True if t is an availability probe ('are we back?') rather than a real queued note."""
    n = _norm(t).lower().rstrip("?!. ")
    if n in _PROBE_EXACT:
        return True
    if len(n) <= 30 and any(k in n for k in _PROBE_KEYWORDS):
        return True
    return False


def queued_prompts(recs):
    """Every unanswered human note queued AFTER the last real assistant work turn, de-noised.

    During a usage/limit down phase the user often queues several valuable notes into a
    blocked session; none get a reply. Harvesting only the trailing prompt (what the banner
    quotes) loses the earlier ones. So: find the last assistant turn that did real work
    (non-empty, not an error), then collect every human turn after it, skipping bare
    availability probes. Order preserved. Returns [] when nothing was queued.
    """
    last_work_idx = -1
    for i, o in enumerate(recs):
        m = o.get("message", {})
        if m.get("role") == "assistant":
            at = _assistant_text(m)
            if at and not _is_error_turn(o, at):
                last_work_idx = i
    out = []
    for o in recs[last_work_idx + 1:]:
        m = o.get("message", {})
        if m.get("role") == "user":
            t = _human_text(m)
            if t and not _is_probe_text(t):
                out.append(t)
    return out


def classify(path):
    """Return dict: interrupted, has_work, dangling, reason ('limit-kill'|'stalled'|'')."""
    recs = _records(path)
    if not recs:
        return {"interrupted": False, "has_work": False, "dangling": "", "reason": ""}
    last_prompt = ""
    last_human_idx = -1
    last_assistant_is_error = False
    work = 0
    for i, o in enumerate(recs):
        if o.get("type") == "last-prompt":
            last_prompt = o.get("lastPrompt") or ""
        m = o.get("message", {})
        role = m.get("role")
        if role == "user" and _human_text(m) is not None:
            last_human_idx = i
        elif role == "assistant":
            at = _assistant_text(m)
            last_assistant_is_error = _is_error_turn(o, at)
            if at and not last_assistant_is_error:
                work += 1
    has_work = work >= 1
    last_human = _human_text(recs[last_human_idx]["message"]) if last_human_idx >= 0 else ""

    if last_assistant_is_error:
        return {"interrupted": True, "has_work": has_work,
                "dangling": last_prompt or last_human, "reason": "limit-kill"}
    if last_human_idx >= 0:
        answered = any(recs[j].get("message", {}).get("role") == "assistant"
                       for j in range(last_human_idx + 1, len(recs)))
        if not answered:
            return {"interrupted": True, "has_work": has_work, "dangling": last_human, "reason": "stalled"}
        if last_prompt and _norm(last_prompt)[:60] != _norm(last_human)[:60]:
            return {"interrupted": True, "has_work": has_work, "dangling": last_prompt, "reason": "stalled"}
    elif last_prompt:
        return {"interrupted": True, "has_work": has_work, "dangling": last_prompt, "reason": "stalled"}

    return {"interrupted": False, "has_work": has_work, "dangling": "", "reason": ""}


def _mtime_str(path):
    import datetime
    return datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")


def _prior_files(proj, current_sid):
    files = [f for f in glob.glob(os.path.join(proj, "*.jsonl"))
             if os.path.basename(f) != "%s.jsonl" % current_sid]
    files.sort(key=os.path.getmtime, reverse=True)
    return files


def _recommended(proj, current_sid):
    """Auto-mode candidate: the most recent SUBSTANTIVE session, iff it was interrupted.
    A clean substantive session (you moved on) suppresses; a killed/empty one is skipped
    so the offer re-appears. Returns (path, info) or (None, None)."""
    for f in _prior_files(proj, current_sid):
        info = classify(f)
        if not info["has_work"]:
            continue
        return (f, info) if info["interrupted"] else (None, None)
    return (None, None)


def _emit_auto(path, info, others):
    ts = _mtime_str(path)
    d = _quote(info["dangling"])
    queued = queued_prompts(_records(path))
    extra = ("" if others <= 0 else
             " (%d other unanswered prompt%s also exist — ask me to list them.)"
             % (others, "s" if others != 1 else ""))
    # Reason-aware wording: a limit-kill answered the prompt and then died mid-work, so
    # "the request was never completed" (only true for a stall) would misreport it.
    if info["reason"] == "limit-kill":
        line = "Last session (%s) was cut off by a usage/API limit mid-task." % ts
        req = "Last request: \"%s\"" % d
    else:
        line = "Last session (%s) left a request unanswered." % ts
        req = "Unfinished: \"%s\"" % d
    # Boxed WARNING banner: a SessionStart hook's systemMessage can't emit ANSI colour, so
    # prominence comes from box rules + a caps ⚡ header + blank spacing, not colour. Made
    # visually dominant so it's hard to overlook regardless of where it lands relative to
    # other plugins' SessionStart lines (cross-plugin ordering isn't controllable here).
    rule = "━" * 46
    lines = [rule, "⚡ INTERRUPTED SESSION — likely unfinished work", line, req]
    if queued:
        lines.append("＋ %d queued note%s from that session — ask me to surface them."
                     % (len(queued), "s" if len(queued) != 1 else ""))
    lines += ["Say \"continue\" to resume, or \"list interrupted\" to browse.", rule]
    banner = "\n".join(lines)
    ctx = ("resume-interrupted: your most recent substantive session (%s) appears to have been "
           "interrupted mid-task (unanswered prompt or usage-limit/API error), so no note that "
           "the work was unfinished could be written at the time. Likely dangling request: \"%s\". "
           "Proactively offer to pick up where it left off — read the tail of that session's "
           "transcript to recover context, then continue. If the user has clearly moved on, "
           "mention it once and don't push.%s" % (ts, d, extra))
    if queued:
        # Keep this universal: surface the lost notes and let the user decide how to keep
        # them (a follow-up, a to-do, whatever tool they use). Do NOT name a specific
        # sibling plugin here — this feature stands alone and must work with none installed.
        ctx += (" That session also has %d earlier note%s queued during the down phase that never "
                "got a reply — surface these and offer to help the user capture each as a "
                "follow-up so it isn't lost: %s"
                % (len(queued), "s" if len(queued) != 1 else "",
                   "; ".join("\"%s\"" % _norm(q)[:120] for q in queued)))
    print(json.dumps({"systemMessage": banner,
                      "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))


def _project_dir_from_cwd():
    enc = os.getcwd().replace("/", "-").replace(".", "-")
    return os.path.join(os.path.expanduser("~/.claude/projects"), enc)


def _run_list(argv):
    proj = None
    if "--dir" in argv:
        try:
            proj = argv[argv.index("--dir") + 1]
        except Exception:
            proj = None
    if not proj:
        proj = _project_dir_from_cwd()
    if not os.path.isdir(proj):
        print("No project transcript directory found (%s)." % proj)
        return
    rows = []
    for f in _prior_files(proj, current_sid=""):
        info = classify(f)
        if info["interrupted"]:
            rows.append((f, info))
    if not rows:
        print("No interrupted sessions found in this project.")
        return
    rec_path = None
    for f, info in rows:  # rows are newest-first; first substantive interrupted = recommendation
        if info["has_work"]:
            rec_path = f
            break
    print("Interrupted sessions in this project (most recent first):\n")
    for f, info in rows:
        mark = "> RECOMMENDED" if f == rec_path else "             "
        kind = "work " if info["has_work"] else "probe"
        d = _norm(info["dangling"])[:80]
        print("  %s  %s  [%s]  %-10s  \"%s\"" % (mark, _mtime_str(f), kind, info["reason"], d))
        # Surface every note queued into that session during its down phase, so multi-note
        # queues aren't lost to the single trailing quote above (each is a candidate follow-up).
        queued = queued_prompts(_records(f))
        for note in queued:
            print("                   · queued: \"%s\"" % (_norm(note)[:90]))
    print("\n  '>' = most likely the one to resume (most recent session with real work).")
    print("  [work] had substantive work; [probe] only an unanswered prompt — usually a failed")
    print("  availability check, but shown in case it was a real request typed on a dead connection.")


def _run_auto():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    tp = data.get("transcript_path") or ""
    sid = data.get("session_id") or ""
    if not tp:
        return
    proj = os.path.dirname(tp)
    if not os.path.isdir(proj):
        return
    path, info = _recommended(proj, sid)
    if not path:
        return
    others = sum(1 for f in _prior_files(proj, sid)
                 if f != path and classify(f)["interrupted"])
    _emit_auto(path, info, others)


def main():
    if "--list" in sys.argv:
        _run_list(sys.argv)
    else:
        _run_auto()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
