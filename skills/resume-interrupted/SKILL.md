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
4. **Reconstruct intent — read back AT LEAST 3 real turns before the interruption point,
   not just the dangling prompt itself.** A single trailing user message is not the full
   picture: it can be a *queued* note sent during the down-phase while an earlier action
   was still in flight, and the thing that actually needs redoing is that earlier action —
   not the note. Walk back through the preceding tool_use/tool_result/text turns until you
   can answer: what was the assistant actually doing right before the interruption, did
   any tool_use call fail to complete (a blocked/errored tool_result, a call with no
   matching result, a transient "temporarily unavailable" bounce), and could redoing the
   wrong thing or skipping the real unfinished step risk lost work, a half-applied edit, or
   data corruption? If 3 turns back still doesn't reach a clear picture, keep walking
   further — 3 is a floor, not a cap. Summarize what's unfinished (including any
   in-flight/aborted action found this way) back to the user *before* diving in, so they
   can confirm or redirect.
5. **Continue** — pick up the task, or ask one crisp clarifying question if the intent is
   ambiguous. If the user has clearly moved on, mention the unfinished item once and drop it.

## Listing all interrupted sessions (browse & pick)

The SessionStart hook only *auto-offers* the single most likely session, and only until
you've had a clean working session (it won't nag). When the user wants to see everything
they might not have picked back up — "what did I leave unfinished?", "list interrupted
sessions", "show me past sessions to resume" — run the detector in list mode.

> **Path caveat — do not paste `$CLAUDE_PLUGIN_ROOT` into the Bash tool.** That variable
> is set only while a *hook* runs; it is **empty in the Bash-tool shell**, so
> `"$CLAUDE_PLUGIN_ROOT/hooks/detect-interrupted.py"` resolves to `/hooks/...` and fails
> with "No such file". Build an absolute path instead. The `hooks/` dir sits **two levels
> up** from this skill's base directory (the "Base directory for this skill" path shown
> when the skill loaded), so use:
>
> ```
> python3 "<SKILL_BASE_DIR>/../../hooks/detect-interrupted.py" --list
> ```
>
> If you don't have the base dir handy, resolve the newest installed copy (version- and
> marketplace-agnostic):
>
> ```
> python3 "$(ls -dt ~/.claude/plugins/cache/*/resume-interrupted/*/hooks/detect-interrupted.py | head -1)" --list
> ```

(Add `--dir <project transcripts dir>` to target a specific project.) It prints
every interrupted session, most recent first, and marks the most likely resume candidate
with `>`. Present it as a short table and ask which one to jump back into.

**Probe transparency:** sessions whose only content is a single unanswered prompt are
labelled `[probe]` (usually a failed "are we back yet?" availability check). They're the
*low-confidence* rows — but **show them anyway**: occasionally the user typed a real
request assuming the connection was already restored, so a probe can matter. Recommend
the `[work]` candidate, but surface the probes so the user can spot a lost real request.

## Also check sessions the standard detector can't see

The SessionStart detector and the procedure above only see `~/.claude/projects/<encoded-cwd>/`.
Some setups run Claude Code through a **proxy, relay, or alternate client** that does **not**
persist a native transcript there — e.g. a local-model session routed via a relay. Those
sessions are invisible to the detector, so unfinished work *and any decisions made in them*
won't be auto-offered on resume, even though they may be the most recent context.

**On resume, if this environment is known to run such sessions, also review their secondary
transcript store** (wherever the proxy/relay mirrors sessions) before concluding you're
caught up. Skim the most recent one(s) for two things:

- **developments or decisions** the current session should know about (a rule the user
  established there, a design choice, a half-finished change), and
- **anything left in a bad state** worth correcting — a model running in that environment may
  have made mistakes a review session should fix: invented/wrong paths, malformed tool
  arguments (e.g. an id passed as a number when the schema wants a string), or assumptions
  stated as settled fact.

Where those transcripts live, and the specific mistakes to look for, are environment-specific
— record them in your project/user notes so this step is concrete rather than abstract.

## Guardrails

- **Verify, don't assume.** Confirm the interruption from the transcript before asserting
  it; never tell the user "there's nothing to resume" without looking.
- **Read excerpts, not entire large transcripts** — but the excerpt must cover at least
  the last 3 real turns before the interruption (step 4), not just the single last one.
  A fixed one-line tail is enough to *detect* (E)/(S); it is not enough to *reconstruct*
  what needs redoing.
- **One reminder, not nagging.** The hook already stops once you've had a clean
  substantive session; mirror that restraint in conversation.
