---
name: resume-interrupted
description: Use when a session may have been cut off mid-task — when the user says "continue", "pick up where we left off", "did that finish?", asks to list/see which past sessions were interrupted or left unfinished, or a SessionStart notice reports an interrupted prior session, or the general sentiment implies unfinished business from a recent session. Recovers context from the transcript and continues the interrupted work.
---

# Resume interrupted work

A session can die at any moment — a usage/credit limit, a crash, a dropped connection.
When it does, **no compute happens afterward**, so nothing records that the work was
unfinished. The interruption leaves no note in memory; the only trace is the transcript
itself. This skill recovers that context and continues.

## When this fires

- The companion SessionStart hook detected an interrupted prior session and injected a
  notice, **or**
- the user asks to "continue" / "pick up where we left off" / "did that finish?", **or**
- the general sentiment implies unfinished business from a recent session (not just that
  exact phrasing).

## How to recognize an interrupted session

A session was interrupted (rather than ended intentionally) if **either**:

- **(E) Limit / API kill** — its **last** assistant turn is an API/budget/limit error.
  The reliable signal is the transcript record's own `isApiErrorMessage` / `apiErrorStatus`
  marker (wording-agnostic — it survives error-message changes and covers overloaded /
  rate-limit / server errors), **not** the specific error text. A real kill turn is a short
  terminal stub; a healthy turn that merely *quotes* an error phrase is NOT a kill.
- **(S) Stalled** — the final human input never got an assistant reply. Two shapes:
  the last human message has no assistant turn after it, **or** the final prompt exists
  only in a trailing `last-prompt` marker (interrupted before it became an answered turn).

Distinguish real interrupted work from a bare **availability probe** ("are we back yet?"):
a resumable session has **≥1 real assistant turn** before the interruption. A session that
is *only* an unanswered prompt has nothing to resume — skip it and look at the next.

**Mid-stream kills** (a reply cut off while streaming) need no special rule: a persisted
assistant turn carries a terminal `stop_reason`, so an interrupted stream generally leaves
either *no* assistant turn (→ falls to **(S)**) or an error turn (→ **(E)**) — both already
covered. Do **not** try to detect truncation by missing end-punctuation or incomplete
words: complete turns routinely end on a colon, a list item, a code fence, a number, or a
tool call with no trailing text, so prose-shape matching false-positives. If a real
interrupted-stream transcript ever surfaces, the principled signal is a missing/`null`
`stop_reason` (deterministic), not prose shape.

## Recovery procedure

1. **Locate the transcripts.** Sessions for the current project live in one directory,
   one `*.jsonl` file per session. A SessionStart hook receives the current session's
   `transcript_path` on stdin; sibling files in that same directory are the prior
   sessions. (In Claude Code this is `~/.claude/projects/<encoded-cwd>/`, where
   `<encoded-cwd>` is the working directory with `/` replaced by `-`.)
2. **Pick the candidate.** Most recent **substantive** session (has real assistant work).
   If it ended cleanly, there's nothing to resume — the user has moved on. If it matches
   (E) or (S), it's the one.
3. **Anchor to the end, then walk backward — don't just read a fixed tail.** A fixed
   line count can open *below* the turn that matters and miss it. Instead find the **last
   real message turn** (skip `last-prompt`, `system`, and `attachment` artifacts) and read
   what precedes it. Decide from structure:
   - last real turn is an **assistant error** (`isApiErrorMessage` / `apiErrorStatus`) → (E);
   - last real **user** turn with **no assistant turn after it** → (S) stalled;
   - a trailing `last-prompt` marker is **not** itself a dangling prompt — if the user turn
     it echoes was already answered, the session is **clean**. It signals a stall only when
     there is *no* corresponding answered user turn.
   Each real record has `type` / `message.role`, a `content` array (text / tool_use /
   tool_result), and a `timestamp`. **Timestamps:** `last-prompt` markers carry **none**,
   and the file's mtime is *not* the time of any turn — when you cite times to the user,
   say which is which (file-modified vs turn-occurred) or you'll manufacture contradictions.
4. **Reconstruct intent.** Identify the unfinished task from the dangling prompt and the
   work that preceded it. Summarize back to the user what looks unfinished *before*
   diving in, so they can confirm or redirect.
5. **Continue** — pick up the task, or ask one crisp clarifying question if the intent is
   ambiguous. If the user has clearly moved on, mention the unfinished item once and drop it.

## Listing all interrupted sessions (browse & pick)

The SessionStart hook only *auto-offers* the single most likely session, and only until
you've had a clean working session (it won't nag). When the user wants to see everything
they might not have picked back up — "what did I leave unfinished?", "list interrupted
sessions", "show me past sessions to resume" — run the detector in list mode:

```
python3 "$CLAUDE_PLUGIN_ROOT/hooks/detect-interrupted.py" --list
```

(Or `--list --dir <project transcripts dir>` to target a specific project.) It prints
every interrupted session, most recent first, and marks the most likely resume candidate
with `>`. Present it as a short table and ask which one to jump back into.

**Probe transparency:** sessions whose only content is a single unanswered prompt are
labelled `[probe]` (usually a failed "are we back yet?" availability check). They're the
*low-confidence* rows — but **show them anyway**: occasionally the user typed a real
request assuming the connection was already restored, so a probe can matter. Recommend
the `[work]` candidate, but surface the probes so the user can spot a lost real request.

## Guardrails

- **Verify, don't assume.** Confirm the interruption from the transcript before asserting
  it; never tell the user "there's nothing to resume" without looking.
- **Read excerpts, not entire large transcripts** — the tail is almost always enough.
- **One reminder, not nagging.** The hook already stops once you've had a clean
  substantive session; mirror that restraint in conversation.
