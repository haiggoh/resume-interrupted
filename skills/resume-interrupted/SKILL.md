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

## Before starting a task that has recorded history

If the task you are picking up has history anywhere — an interrupted session, a to-do or
follow-up note, a plan file, project notes — read that history **first**, before planning
or re-deriving anything. A prior pass may already have found what you are about to look
for, and the constraints the user recorded there still bind even though the session that
captured them is gone. Treating the note as a reminder of the *topic* rather than a record
of *decisions* re-derives findings at best and silently violates a recorded constraint at
worst.

To find the right session when its last prompt is not a good hint — the `interrupted` list
orders by last prompt — search transcripts by topic instead: `grep -l` across the session
files (`~/.claude/projects/*/*.jsonl`), then read the forks under the session's
`subagents/` directory.

A session the user names by its window title ("📡 <model> · <2 chars>") is found by
content, never by the title: that suffix is a random per-launch disambiguator, not a
session-id prefix (grepping for it as a filename finds weeks-old false hits). Scan
transcript HEADS instead — each session's own records embed the 📡 window name and the
`Actual model:` banner its launcher hook wrote, which survives even after the registry
record is cleaned up on exit.

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
- **(M) Mid-tool kill** — the last assistant turn emitted a `tool_use` whose result never
  arrived: the session died between the call and the tool's completion. Neither (E) (no
  error turn — the death beat the API reply) nor (S) (the prompt WAS answered — work was
  underway). Structural and wording-agnostic: a persisted `tool_use` id with no matching
  `tool_result` anywhere after it.

**Precedence is (E) > (M) > (S).** The kinds are not exclusive in reality — a budget kill can
land while a tool call is in flight — so when a persisted error turn exists it wins over an
unanswered `tool_use`, and the session is treated as an (E) death. When the kill beat the API
reply and left **no** error turn, the transcript cannot say why it died: (M) and (S) name the
shape, not the cause. Do not read "mid-tool" as "not a budget kill"; if the user or a ledger says
it was one, apply the reset-boundary check below by hand (`interrupted tail` prints it).

Distinguish real interrupted work from a bare **availability probe** ("are we back yet?"):
a resumable session has **≥1 real assistant turn** before the interruption. A session that
is *only* an unanswered prompt has nothing to resume — skip it and look at the next.

**Mid-stream kills** (a reply cut off while streaming) mostly need no special rule: a
persisted assistant turn carries a terminal `stop_reason`, so an interrupted stream leaves
either *no* assistant turn (→ **(S)**), an error turn (→ **(E)**), or a `tool_use` awaiting
its result (→ **(M)**) — all covered. Do **not** try to detect truncation by missing
end-punctuation or incomplete words: complete turns routinely end on a colon, a list item,
a code fence, a number, or a tool call with no trailing text, so prose-shape matching
false-positives. (Real case, 2026-10-25: a 7-hour autonomous session died between a Bash
`tool_use` and its result, matched neither (E) nor (S), and was invisible to every
automated resume surface — which is why (M) exists.)

**A session that is still alive is never a resume candidate.** The launcher's registry
(`~/.claude/sessions/<pid>.json`) records live sessions; the detector excludes them (pid
alive **and** its recorded start time matching the process — a recycled pid is a different
process wearing the same number). When no registry can answer, a transcript written in the
last ~10 minutes is hedged as "may still be running" instead of confidently stalled. Never
double-drive a session on that hedge: confirm from the registry or the terminal before
writing anything it might also write.

## A limit kill carries a constraint — check whether it has expired

**(E) and (S) differ in one way that outlives the interruption.** A crash or a dropped
connection tells you nothing about the world now. A **usage/credit/quota kill** does: it
says "you were out of allowance." That statement was true at the moment of death and
becomes false again at the provider's next reset — so when you resume a session that died
this way, establish whether the constraint is still current **before** you act on it.

- **Different reset period than the kill → the old limit is not in force.** Do not warn the
  user about it, do not treat it as a current constraint, and do not quietly scale the plan
  down because of it. Yesterday's exhausted allowance is not evidence about today's.
- **Same reset period → it may still apply.** Say "may", and confirm against a live signal.
- **Applies to (E) only.** A crash or dropped connection gets no such inference — there was
  no constraint to expire.
- **Never invent the allowance.** The finding is *"a reset boundary has passed"*, which
  needs no cap value, and no assumption that a cap exists at all. If something in the
  environment tracks cost or limits, read the current figure from it; if nothing does, say
  the state is unknown rather than guessing a number.

The companion hook does this arithmetic for a limit kill it detects and states the verdict
in its notice. Doing it by hand: compare the **reset period** containing the kill against
the one containing now — not the calendar date. The boundary is the reset time **plus the
provider's propagation lag** (default 00:00 UTC + 10 min, configurable via
`RESUME_INTERRUPTED_RESET_UTC_HOUR` / `RESUME_INTERRUPTED_RESET_PROPAGATION_MIN`), so a kill
at 23:50 and a resume at 00:05 are the *same* period and nothing has reset yet.

**Which timestamp you used matters here.** Prefer the transcript's own last `timestamp`
(the real event time) over the file's mtime, and say which one you read — the day boundary
is exactly where the two can disagree and turn a correct verdict into a wrong one.

## Recovery procedure

1. **Locate the transcripts.** Sessions for the current project live in one directory,
   one `*.jsonl` file per session. A SessionStart hook receives the current session's
   `transcript_path` on stdin; sibling files in that same directory are the prior
   sessions. (In Claude Code this is `~/.claude/projects/<encoded-cwd>/`, where
   `<encoded-cwd>` is the working directory with `/` replaced by `-`.)
2. **Pick the candidate.** Most recent **substantive** session (has real assistant work).
   If it ended cleanly, there's nothing to resume — the user has moved on. If it matches
   (E) or (S), it's the one.
   **Shortcut for steps 3–4: `interrupted tail <session-id-or-path>`.** Read-only. It prints the
   detector's verdict, the last conversational records condensed (tool calls and results
   included, errors flagged), every unanswered prompt (`queue-operation` records tagged as
   such), any unanswered `tool_use`, the reset-boundary check, and — when the audit-loose-ends
   plugin is installed — its digest of what that session changed (silently skipped when it is
   not). Widen with `--records N`; `--no-audit` skips the digest; `--json` for a parseable form.
   You still read back past what it shows before summarising to the user.
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
   During a down phase the user often queues **several** notes into a stalled session, not
   one — a single file can hold half a dozen unanswered prompts, and the trailing one is
   not necessarily the most valuable. Harvest **every** unanswered user turn in the file
   (skip slash-command echoes, tool results, and "still blocked?"-type probe noise), then
   triage what you collected.
   Harvest `queue-operation` records too: a prompt typed mid-turn can exist ONLY as such a
   record (absorbed into the running turn, never a user turn), and its content is the only
   trace of what the user asked for. Also sweep the session's sidecar files, its
   attachment records, and `~/.claude/history.jsonl` (filter by `sessionId`) for prompts
   that never landed in the transcript at all.
5. **Continue** — pick up the task, or ask one crisp clarifying question if the intent is
   ambiguous. If the user has clearly moved on, mention the unfinished item once and drop it.

## Listing all interrupted sessions (browse & pick)

The SessionStart hook only *auto-offers* the single most likely session, and only until
you've had a clean working session (it won't nag). When the user wants to see everything
they might not have picked back up — "what did I leave unfinished?", "list interrupted
sessions", "show me past sessions to resume" — run the detector in list mode.

```
interrupted
```

That is the whole command. Claude Code puts an enabled plugin's `bin/` directory on the
Bash tool's `PATH`, so no path construction is needed — and **do not build one**. In
particular, do not paste `$CLAUDE_PLUGIN_ROOT`: it is set only while a *hook* runs and is
**empty in the Bash-tool shell**, so `"$CLAUDE_PLUGIN_ROOT/hooks/..."` resolves to
`/hooks/...` and fails with "No such file".

Useful variants:

```
interrupted recommended                 # just the one most worth resuming
interrupted --project "$HOME"           # a different project, by its real path
interrupted --limit 10                  # page a long list
interrupted --json                      # complete machine-readable output
interrupted --help                      # the full surface
```

Pass `--project <path>` with the project's **real working directory**; the command does
Claude Code's transcript-directory encoding itself, so never hand-build a
`~/.claude/projects/-Users-…` path. (`--dir` still takes an already-encoded directory if
you have one.)

> **Fallback, only if `interrupted: command not found`** — that means the plugin's `bin/`
> isn't on `PATH` (an older Claude Code, or the plugin isn't enabled). Then call the
> script directly, resolving the newest installed copy version- and marketplace-agnostically:
>
> ```
> python3 "$(ls -dt ~/.claude/plugins/cache/*/resume-interrupted/*/hooks/detect-interrupted.py | head -1)" --list
> ```

It prints every interrupted session, most recent first, and marks the most likely resume
candidate with `>`. Present it as a short table and ask which one to jump back into.

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

## A killed background workflow leaves a journal

When the interrupted session died while a **background workflow** was running, its ephemeral
task-output file is usually gone days later — but the workflow's own `journal.jsonl` (the
resume journal) and the per-agent transcripts under the session's
`subagents/workflows/<runId>/` directory survive, and they hold every completed `agent()`
result. A completed-but-unsaved workflow output (drafted documents, findings, plans) can
therefore be recovered from the journal even after the temp files are wiped. Check there
before concluding the work is lost.

## Guardrails

- **Verify, don't assume.** Confirm the interruption from the transcript before asserting
  it; never tell the user "there's nothing to resume" without looking.
- **Read excerpts, not entire large transcripts** — but the excerpt must cover at least
  the last 3 real turns before the interruption (step 4), not just the single last one.
  A fixed one-line tail is enough to *detect* (E)/(S); it is not enough to *reconstruct*
  what needs redoing.
- **One reminder, not nagging.** The hook already stops once you've had a clean
  substantive session; mirror that restraint in conversation.
- **Don't inherit an expired limit.** After a usage/quota kill, resuming in a later reset
  period means that limit is history. Reporting it as current is the same class of error as
  claiming there's nothing to resume without looking — a confident statement about a state
  you didn't check.
- **Transcripts double as an instruction-file version history.** Every transcript embeds
  the instruction file (CLAUDE.md or equivalent) that was active when the session ran, so
  old transcripts are also a record of what rules were in force at the time — useful when
  you need to prove what an earlier session was actually told to do.
- **Session-summary files are not the user's words.** A summary written while the session
  ran was produced by whatever model the session used, so a "quote" in it can be translated
  or paraphrased. Before attributing words to the user, read the transcript's own user
  records; the summary is a pointer, never the source.
