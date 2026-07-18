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
only; reads transcripts.

Optional cross-plugin coordination, downstream: after deciding whether to print a
banner, this hook writes a session-scoped "done" flag to
`$TMPDIR-or-/tmp/claude-sessionstart-banners/<session_id>.resume-interrupted.done`
— always, whether or not it printed. Any OTHER plugin's SessionStart hook may poll for
that file (with its own short, bounded timeout) to sequence its own banner after this
one (waypoints does this), without resume-interrupted knowing or caring that the other
plugin exists. One-way, best-effort, presence-only.

Optional cross-plugin coordination, upstream: symmetrically, if no-hidden-changes is
installed AND enabled (checked via ~/.claude/settings.json's `enabledPlugins`, never a
code import), this hook briefly polls no-hidden-changes' own analogous flag
(`<session_id>.no-hidden-changes.done`) before deciding its own banner — so
no-hidden-changes' banner (meant to read as the most foundational/always-on notice)
lands first, this one second, and waypoints' (via the downstream flag above) third.
Waiting is capped at BANNER_WAIT_S and always falls through regardless of whether the
flag showed up — this hook must never suppress or meaningfully delay its own banner
just because no-hidden-changes is slow, absent, or the flag format changes. If
no-hidden-changes isn't installed/enabled, no wait happens at all.

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

Orphaned queued notes (secondary surfacing): the clean-session suppression above stops the
full resume offer, but it must NOT silently bury real notes a user typed into dead-end probe
sessions AFTER the newest substantive session ("I fixed that bug and pushed it", typed while
checking "are we back yet?" during a down phase). New sessions are created per retry, so
those probes are chronologically NEWER than the clean top-of-stack — the clean session being
older does not mean the user has moved past notes typed after it. So when the full offer is
suppressed, we still scan the NEWER, has_work=False probes ahead of the clean top (bounded to
a recent window, ORPHANED_NOTE_WINDOW_S, and stopping at the first has_work=True session in
that range) and, if any hold queued notes, emit a distinct, lightweight one-shot notice (NOT
the resume banner). This surfaces the content without reopening the moved-on task or nagging.

Platform limitation (not fixable here): a sufficiently abrupt kill can terminate the client
before ANYTHING is flushed to the session's own .jsonl — no error turn, no dangling prompt,
no queued note, nothing on disk. Such a session leaves zero transcript trace, so it is
unrecoverable by design; there is nothing for this script's logic to detect or resume. This
is a Claude Code platform behaviour, not a bug in this detector.
"""

import sys, os, json, glob, time

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


def _is_stale_last_prompt(recs, last_prompt):
    """True if last_prompt merely echoes an EARLIER human turn that already received an
    assistant reply, rather than genuinely new, unanswered input.

    Claude Code's last-prompt marker tracks the last plain-text prompt but isn't refreshed
    by slash-command/skill invocations — so after a command round-trips cleanly, the file
    can still end with one or more last-prompt records echoing the prompt from BEFORE that
    command, making an already-answered session look like it has fresh dangling input.
    """
    norm_lp = _norm(last_prompt)[:60]
    if not norm_lp:
        return False
    for i, o in enumerate(recs):
        m = o.get("message", {})
        if m.get("role") != "user":
            continue
        t = _human_text(m)
        if t and _norm(t)[:60] == norm_lp:
            if any(recs[j].get("message", {}).get("role") == "assistant" for j in range(i + 1, len(recs))):
                return True
    return False


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
        if (last_prompt and _norm(last_prompt)[:60] != _norm(last_human)[:60]
                and not _is_stale_last_prompt(recs, last_prompt)):
            return {"interrupted": True, "has_work": has_work, "dangling": last_prompt, "reason": "stalled"}
    elif last_prompt and not _is_stale_last_prompt(recs, last_prompt):
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


# How far forward (in wall-clock terms, i.e. how recent relative to now) to look for
# orphaned queued notes when the full resume-offer is suppressed. Rationale: queued notes
# are near-term, actionable follow-ups ("I fixed the bug and pushed it", "important data
# point") worth surfacing for a couple of weeks, but NOT resurrecting a note from months ago
# the first time this check happens to run. 14 days is a deliberately conservative window:
# long enough to cover a realistic gap between the dead-end probes and the next real session,
# short enough that stale threads go quiet on their own.
ORPHANED_NOTE_WINDOW_S = 14 * 24 * 60 * 60


def _orphaned_queued_notes(proj, current_sid):
    """Queued notes stranded by suppression, from dead-end probes typed AFTER the last clean
    substantive session.

    Real-world shape this fixes (confirmed against this project's own transcripts, not
    hypothetical): a session dies on a kill abrupt enough to leave NOTHING on disk (the
    separately-documented, unfixable platform limitation). The user retries into a brand new
    session; if the API is still down, THAT one is a bare probe too (has_work=False) — often
    several in a row, each just an unanswered "are we back yet?" or, sometimes, a real note
    ("heads up: I manually fixed a bug you created, already pushed") typed while checking.
    Eventually the API recovers and a genuine, clean, substantive session happens.

    _recommended() correctly walks past those has_work=False probes to find the newest
    SUBSTANTIVE session for the resume decision — but that substantive session is
    chronologically OLDER than the probes (new session per retry means retries sort newer,
    since they were created later in wall-clock time). So "the newest substantive session is
    clean" does NOT mean the user has seen or moved past notes queued into probes typed AFTER
    it — it means the opposite: those probes came LATER and are still unacknowledged.

    So this walks the NEWER end of `_prior_files`' newest-first list — i.e. everything before
    the clean top-of-stack substantive session index — collecting queued notes from
    has_work=False probes. It stops if it hits ANY has_work=True session in that newer range:
    that would mean real work actually happened after the probes, which is the caller's
    responsibility (either _recommended's own check, if newest, or a sign the probes were
    already superseded by acknowledged work) — not this function's to reach past.

      - Only runs when the top-of-stack substantive session is clean (suppression active).
        If the newest substantive session was itself interrupted, the full resume banner
        fires instead and this is not consulted.
      - Bounded by ORPHANED_NOTE_WINDOW_S measured back from NOW (not from the clean top's
        mtime — these probes are typically newer than "now minus a bit", so anchoring on the
        clean top would under-cover exactly the sessions we care about). A probe older than
        the window is treated as stale and dropped.

    An "older than a clean session, from an even earlier interrupted session" case was also
    considered (walking backwards past top_idx) but is not exercised here: given sessions are
    created strictly forward in time on each retry, an interrupted session that predates a
    clean session was, by construction, already superseded by that later clean session — the
    user chronologically moved past it. Only the newer-than-clean-top direction reflects how
    sessions actually get created.

    Returns a list of (path, mtime_str, [notes...]) newest-first, or [] if nothing qualifies.
    """
    files = _prior_files(proj, current_sid)
    # Find the newest substantive session and confirm it's the clean one that suppressed.
    top_idx = None
    for i, f in enumerate(files):
        if classify(f)["has_work"]:
            top_idx = i
            break
    if top_idx is None:
        return []
    top = files[top_idx]
    if classify(top)["interrupted"]:
        return []  # newest substantive session is interrupted -> full banner handles it
    cutoff = time.time() - ORPHANED_NOTE_WINDOW_S
    out = []
    for f in files[:top_idx]:  # newer than the clean top, newest-first
        info = classify(f)
        if info["has_work"]:
            break  # real work happened after the probes -> not this function's to reach past
        try:
            if os.path.getmtime(f) < cutoff:
                continue  # this probe is stale; a newer one in the same walk may still qualify
        except OSError:
            continue
        notes = queued_prompts(_records(f))
        if notes:
            out.append((f, _mtime_str(f), notes))
    return out


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
    # U+26A1 defaults to TEXT presentation without an explicit VS16 (U+FE0F) suffix, so most
    # terminals render it as a plain glyph, not the colour emoji — unlike waypoints' U+1F9ED,
    # which defaults to emoji presentation on its own.
    rule = "━" * 46
    lines = [rule, "⚡️ INTERRUPTED SESSION — likely unfinished work", line, req]
    if queued:
        lines.append("＋ %d queued note%s from that session:"
                     % (len(queued), "s" if len(queued) != 1 else ""))
        for note in queued:
            lines.append("  · \"%s\"" % _quote(note, 90))
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
                   "; ".join("\"%s\"" % _quote(q, 120) for q in queued)))
    print(json.dumps({"systemMessage": banner,
                      "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))


def _emit_orphaned_queued_notes(groups):
    """Lightweight surfacing (NOT the full resume banner) of queued notes stranded in
    dead-end probe sessions typed AFTER the last clean substantive session. Distinct wording
    so it never reads as "resume a task" — there's no task to resume, just unacknowledged
    notes typed while checking whether a down phase had cleared, worth capturing before
    they're lost. `groups` is the list from _orphaned_queued_notes()."""
    total = sum(len(notes) for _, _, notes in groups)
    rule = "─" * 46
    lines = [rule,
             "✎ QUEUED NOTES from dead-end retry sessions may be unaddressed",
             "Your last substantive session was clean, but %d note%s typed into later "
             "probe/retry session%s never got a reply:"
             % (total, "s" if total != 1 else "", "s" if total != 1 else "")]
    for _, ts, notes in groups:
        for note in notes:
            lines.append("  · (%s) \"%s\"" % (ts, _quote(note, 90)))
    lines += ["Ask me to help capture any of these as a follow-up, or say \"list interrupted\".", rule]
    banner = "\n".join(lines)
    flat = "; ".join("\"%s\" (%s)" % (_norm(n)[:120], ts)
                     for _, ts, notes in groups for n in notes)
    ctx = ("resume-interrupted: your most recent SUBSTANTIVE session was clean, so the full "
           "resume offer is intentionally suppressed (there's no unfinished task to resume). "
           "HOWEVER, one or more dead-end probe/retry sessions typed AFTER that clean session "
           "(e.g. while checking whether a down phase had cleared) still hold %d user note%s "
           "that never got a reply and would otherwise be lost forever. Do NOT offer to resume "
           "anything — just surface these once and offer to help capture each as a "
           "follow-up/to-do so nothing actionable is dropped: %s"
           % (total, "s" if total != 1 else "", flat))
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


def _banner_flag_dir():
    return os.path.join(os.environ.get("TMPDIR") or os.environ.get("XDG_RUNTIME_DIR")
                         or "/tmp", "claude-sessionstart-banners")


BANNER_WAIT_S = float(os.environ.get("RESUME_INTERRUPTED_BANNER_WAIT_S") or 0.75)
BANNER_POLL_S = float(os.environ.get("RESUME_INTERRUPTED_BANNER_POLL_S") or 0.05)


def _settings_path():
    return os.environ.get("CLAUDE_SETTINGS_FILE") or os.path.expanduser(
        "~/.claude/settings.json")


def _plugin_enabled(slug_prefix):
    """True if any `enabledPlugins` key like '<slug_prefix>@<marketplace>' is truthy.
    Never raises — a missing/malformed settings file just means 'not detected'."""
    try:
        import re
        with open(_settings_path()) as f:
            settings = json.load(f)
        enabled = settings.get("enabledPlugins") or {}
        pat = re.compile(r"^%s@" % re.escape(slug_prefix))
        return any(pat.match(k) and v for k, v in enabled.items())
    except Exception:
        return False


def _wait_for_no_hidden_changes(sid):
    """Presence-only poll for no-hidden-changes' done flag, bounded by BANNER_WAIT_S.
    Content is never parsed — a malformed/stale flag can't cause a false wait, only its
    mere existence matters. No-op if sid is empty or no-hidden-changes isn't enabled."""
    if not sid or not _plugin_enabled("no-hidden-changes"):
        return
    flag = os.path.join(_banner_flag_dir(), "%s.no-hidden-changes.done" % sid)
    deadline = time.monotonic() + BANNER_WAIT_S
    while time.monotonic() < deadline:
        if os.path.exists(flag):
            return
        time.sleep(BANNER_POLL_S)


def _signal_done(sid, printed):
    """Best-effort, session-scoped 'I'm done deciding' flag for any OTHER plugin's
    SessionStart hook to optionally poll on. Never raises; never blocks; sid-less
    sessions (unparseable stdin) get no flag, since nothing could key on them anyway."""
    if not sid:
        return
    try:
        d = _banner_flag_dir()
        os.makedirs(d, mode=0o700, exist_ok=True)
        path = os.path.join(d, "%s.resume-interrupted.done" % sid)
        tmp = path + ".tmp.%d" % os.getpid()
        with open(tmp, "w") as f:
            f.write("producer=resume-interrupted printed=%d\n" % (1 if printed else 0))
        os.replace(tmp, path)
    except Exception:
        pass


def _run_auto():
    sid = ""
    printed = False
    try:
        try:
            data = json.load(sys.stdin)
        except Exception:
            return
        tp = data.get("transcript_path") or ""
        sid = data.get("session_id") or ""
        _wait_for_no_hidden_changes(sid)
        if not tp:
            return
        proj = os.path.dirname(tp)
        if not os.path.isdir(proj):
            return
        path, info = _recommended(proj, sid)
        if not path:
            # Full resume offer is suppressed (newest substantive session is clean, or there
            # is none). Before going fully silent, check whether an earlier interrupted
            # session within the recency window stranded real queued notes — surface those in
            # a distinct, non-nagging way so actionable notes aren't lost to suppression.
            groups = _orphaned_queued_notes(proj, sid)
            if groups:
                _emit_orphaned_queued_notes(groups)
                printed = True
            return
        others = sum(1 for f in _prior_files(proj, sid)
                     if f != path and classify(f)["interrupted"])
        _emit_auto(path, info, others)
        printed = True
    finally:
        _signal_done(sid, printed)


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
