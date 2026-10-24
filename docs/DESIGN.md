# Design notes

Why `resume-interrupted` is shaped the way it is: the detection contract, the decisions that
are easy to re-litigate, and the constraints that are not derivable from the code alone.
Written so the reasoning is public and durable.

## Origin

Born from a session where "pick up where we left off" turned into manually digging through
transcripts — after an assistant claimed it had no prior conversation and nothing on disk,
which was false: the transcripts were right there. Two realizations followed:

1. **A limit-killed session cannot write its own "unfinished" note.** No compute happens
   after the kill, so the transcript is the only trace an interruption leaves.
2. Facts live in memory and notes, but *detection and recovery* need a home that fires at
   the right moment — hence a SessionStart hook plus the bundled skill.

## Detection contract

Interrupted if **(E)** the last assistant turn is an API/budget error, or **(S)** the final
human input is unanswered / an orphaned `last-prompt`.

- **(E) keys on the transcript record's own structural markers** (`isApiErrorMessage` /
  `apiErrorStatus`), not error wording — wording-agnostic, so it survives error-text
  changes and covers error kinds that were never enumerated. A text-signature match
  survives only as a legacy fallback for older transcripts and other gateways. (Measured
  on real transcripts: every quota refusal carried `apiErrorStatus=429` and every
  transport death carried no status at all, so the status field alone separates them.)
- **Only the LAST assistant turn decides**, so a *recovered* mid-session error is not a
  kill.
- **Mid-stream kills need no special rule.** A persisted assistant turn carries a terminal
  `stop_reason`, so an interrupted stream leaves either no assistant turn (→ S) or an error
  turn (→ E). The principled signal for a true partial would be a missing/`null`
  `stop_reason`. **Never** detect truncation from prose shape (missing end-punctuation, an
  incomplete word): complete turns routinely end on a colon, a list item, a code fence or
  a bare tool call.
- **Self-denoising.** Only the most recent *substantive* session (≥1 real assistant turn)
  is considered, so bare "are we back yet?" availability probes are skipped and the offer
  goes quiet once a clean substantive session exists. It is not one-shot: it re-appears
  each startup while the condition holds, so a killed offer-session re-asks.
- **(E) is the exception, not the norm.** Users usually close the terminal during the
  retry countdown, before an error turn is ever persisted — so (S) carries most of the
  load.
- **Never blocks a session.** Any error → exit 0, no output. `python3` is the only
  dependency and its absence no-ops.
- `queued_prompts()` harvests **every** unanswered human note queued after the last real
  assistant turn, not just the trailing one: during a down phase users queue several notes
  into a stalled session.
- `_orphaned_queued_notes()` surfaces notes trapped in dead-end probe sessions even when
  the full resume banner is suppressed — and it walks **newer** files than the clean top,
  because after an abrupt kill each retry creates a *new* session, so the dead ends land
  ahead of it, not behind. (Sessions are created strictly forward in time, so the "older
  interrupted session behind an intervening clean one" shape does not exist.)
- A **skill-injection body arrives as a `role=user` turn**, and Claude Code's `last-prompt`
  marker is NOT refreshed by it — so it must not be read as the last human text. A
  trailing `last-prompt` marker is likewise not itself a dangling prompt: if the user turn
  it echoes was answered, the session is clean.

## De-dup: each text lives in exactly one place

- **Mechanism + recovery procedure** → this plugin (hook + skill).
- **The always-on behavioural imperative** ("check session history before claiming no
  prior context") → the user's instruction file (CLAUDE.md), terse, naming no plugin and
  no path.
- **Design history** → this file.

## Independence

This plugin must stay independent of open-item/reminder plugins: **no runtime dependency
either way**. Banner order between hooks is nevertheless achieved via the one-way
best-effort flag pattern — a session-scoped, bounded, fail-open done-flag that the
consumer polls and then proceeds regardless. The queued-note handoff uses universal
open-item vocabulary and never names another plugin in code.

## Shape

- `.claude-plugin/plugin.json` carries **no `hooks` key**: hooks auto-load from
  `hooks/hooks.json`, and declaring them in the manifest too triggers a "Duplicate hooks"
  install error.
- `hooks/hooks.json` wires the SessionStart hook (matcher `startup`).
- `hooks/detect-interrupted.py` is stdlib-only.
- `bin/interrupted` is the public CLI (`list` / `recommended`, `--project`, `--json`).
- Tests are **script-style**, not pytest-collectable: `tests/test_detect.py` is a
  self-contained PASS/FAIL harness that `sys.exit()`s at module level, so run it directly
  (`python3 tests/test_detect.py`), never through pytest, which dies with an internal
  error on the intentional exit.

## Decisions that were deliberately made (don't re-litigate)

- **Pass B — "(Ø) no file at all"** (a session that ran and did real work but left zero
  bytes on disk) was closed as not relevant: the defect it targeted turned out to be an
  upstream bug, since fixed by a version upgrade.
- **Prefer additive over breaking redesign.** A proposed
  `{primary_interrupted, downtime_notes}` return-shape redesign was rejected as an
  unnecessary breaking change across 5 call sites and ~20 tests; extending `reason`
  additively did the same job.
- A **secondary transcript store** (a proxy/relay/local-model session) is invisible to
  the detector by construction, since it only reads Claude Code's own projects directory.
  The skill tells the operator to review those stores separately on resume instead of
  pretending the detector can see them.
