#!/usr/bin/env python3
"""resume-interrupted — SessionStart hook.

Reads the SessionStart hook JSON on stdin, looks at the MOST RECENT prior session
in the same project, and — if that session was cut off mid-task — emits a one-line,
model-facing notice (hookSpecificOutput.additionalContext) so Claude can proactively
offer to pick up where it left off.

Why this exists: when a session dies on a usage/credit limit, a crash, or a dropped
connection, no compute is possible afterward, so nothing can record that the work was
unfinished. The interruption is therefore invisible to the next session — unless we
detect it from the transcript.

Design guarantees:
  * Never blocks a session. Any error → print nothing, exit 0.
  * Zero third-party deps (stdlib only).
  * Self-denoising: it only looks at your most recent *substantive* session (one with
    real assistant work), skipping bare "are we back yet?" availability probes. If that
    session ended cleanly, it stays silent — so once you've done real work since, it
    stops reminding you.

Interruption markers (either one):
  (E) LIMIT KILL  — the last assistant turn is an API/budget error
                    ("Budget has been exceeded" / "API Error: Request rejected").
  (S) STALLED     — the final human input (last user record, or an orphaned
                    last-prompt marker) never received an assistant reply
                    (covers crashes / dropped connections, or a limit that hit
                    before any reply was produced).
"""

import sys, os, json, glob

# (E) markers. The budget/credit strings are specific to metered gateways; harmless
# elsewhere (those users simply match (S) instead). Extend for your environment.
ERROR_SIGNATURES = ("Budget has been exceeded", "API Error: Request rejected", "usage limit reached")


def _load_stdin():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


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
    """Human-authored prompt text, or None for tool results / command output / non-human."""
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
    if not t or t.lstrip().startswith("<"):  # e.g. <local-command-stdout>, <command-name>
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
    return " ".join((s or "").split())[:60]


def classify(path):
    """Return (interrupted, has_work, dangling_prompt)."""
    recs = _records(path)
    if not recs:
        return (False, False, "")
    last_prompt = ""
    last_human_idx = -1
    last_assistant_text = None
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
            last_assistant_text = at
            if at and not any(sig in at for sig in ERROR_SIGNATURES):
                work += 1
    has_work = work >= 1
    last_human = _human_text(recs[last_human_idx]["message"]) if last_human_idx >= 0 else ""

    # (E) limit kill: the session died on an API/budget error
    if last_assistant_text and any(sig in last_assistant_text for sig in ERROR_SIGNATURES):
        return (True, has_work, last_prompt or last_human)

    # (S) stalled: the last human prompt never got a reply
    if last_human_idx >= 0:
        answered = any(recs[j].get("message", {}).get("role") == "assistant"
                       for j in range(last_human_idx + 1, len(recs)))
        if not answered:
            return (True, has_work, last_human)
        # (S') orphaned last-prompt: a newer prompt that only reached the last-prompt
        # marker (interrupted before it became an answered turn).
        if last_prompt and _norm(last_prompt) != _norm(last_human):
            return (True, has_work, last_prompt)
    elif last_prompt:
        return (True, has_work, last_prompt)

    return (False, has_work, "")


def _mtime_str(path):
    import datetime
    return datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")


def main():
    data = _load_stdin()
    tp = data.get("transcript_path") or ""
    sid = data.get("session_id") or ""
    if not tp:
        return
    proj = os.path.dirname(tp)
    if not os.path.isdir(proj):
        return
    files = [f for f in glob.glob(os.path.join(proj, "*.jsonl"))
             if os.path.basename(f) != "%s.jsonl" % sid]
    files.sort(key=os.path.getmtime, reverse=True)

    # Walk newest -> oldest; stop at the first SUBSTANTIVE session (has real work).
    # Bare probes (no assistant work) are transparent and skipped.
    for f in files:
        interrupted, has_work, dangling = classify(f)
        if not has_work:
            continue
        if interrupted:
            d = _norm(dangling)
            notice = (
                "resume-interrupted: your most recent substantive session (%s) appears to have been "
                "interrupted mid-task — it ended on an unanswered prompt or a usage-limit/API error, so "
                "no note that the work was unfinished could be written at the time. Likely dangling "
                "request: \"%s\". Proactively offer to pick up where it left off: read the tail of that "
                "session's transcript to recover context, then continue. If the user has clearly moved "
                "on to something else, mention it once and don't push." % (_mtime_str(f), d)
            )
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": notice,
            }}))
        return  # stop at the most recent substantive session either way


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
