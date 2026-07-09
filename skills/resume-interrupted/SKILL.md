---
name: resume-interrupted
description: Use when a session may have been cut off mid-task — when the user says "continue", "pick up where we left off", "did that finish?", or a SessionStart notice reports an interrupted prior session, or the general sentiment implies unfinished business from a recent session. Recovers context from the transcript and continues the interrupted work.
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

- **(E) Limit / API kill** — its last assistant turn is an API/budget error
  (e.g. `Budget has been exceeded`, `API Error: Request rejected`). The session died
  trying to respond.
- **(S) Stalled** — the final human input never got an assistant reply. Two shapes:
  the last human message has no assistant turn after it, **or** the final prompt exists
  only in a trailing `last-prompt` marker (interrupted before it became an answered turn).

Distinguish real interrupted work from a bare **availability probe** ("are we back yet?"):
a resumable session has **≥1 real assistant turn** before the interruption. A session that
is *only* an unanswered prompt has nothing to resume — skip it and look at the next.

## Recovery procedure

1. **Locate the transcripts.** Sessions for the current project live in one directory,
   one `*.jsonl` file per session. A SessionStart hook receives the current session's
   `transcript_path` on stdin; sibling files in that same directory are the prior
   sessions. (In Claude Code this is `~/.claude/projects/<encoded-cwd>/`, where
   `<encoded-cwd>` is the working directory with `/` replaced by `-`.)
2. **Pick the candidate.** Most recent **substantive** session (has real assistant work).
   If it ended cleanly, there's nothing to resume — the user has moved on. If it matches
   (E) or (S), it's the one.
3. **Read the tail, not the whole file.** The last ~15–30 records hold the final
   exchange: the dangling prompt, the last thing you were doing, and (for limit kills)
   the error itself. Each line is a JSON record with `type` / `message.role`, a `content`
   array (text / tool_use / tool_result), and a `timestamp`.
4. **Reconstruct intent.** Identify the unfinished task from the dangling prompt and the
   work that preceded it. Summarize back to the user what looks unfinished *before*
   diving in, so they can confirm or redirect.
5. **Continue** — pick up the task, or ask one crisp clarifying question if the intent is
   ambiguous. If the user has clearly moved on, mention the unfinished item once and drop it.

## Guardrails

- **Verify, don't assume.** Confirm the interruption from the transcript before asserting
  it; never tell the user "there's nothing to resume" without looking.
- **Read excerpts, not entire large transcripts** — the tail is almost always enough.
- **One reminder, not nagging.** The hook already stops once you've had a clean
  substantive session; mirror that restraint in conversation.
