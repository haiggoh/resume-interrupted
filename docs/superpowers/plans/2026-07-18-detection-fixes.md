# resume-interrupted: detection fixes (rough plan)

Status: ROUGH DRAFT — scope agreed interactively 2026-07-18, not yet implemented.
Current version at time of writing: 0.2.13.

## Why

A real interruption on 2026-07-17 (user typed a `!`-prefixed line, then "check the flush",
then hit a budget kill) exposed that the detector's two known shapes — (E) persisted API
error, (S) unanswered human turn — don't cover what actually happens most of the time for
this user:

- The user usually closes/exits **during** the ~3-minute retry countdown, before the
  persisted `API Error: ... Budget has been exceeded` turn is ever written. So (E) is the
  **exception**, not the norm, for real kills.
- At least one real session left **zero file on disk at all** — not a truncated tail, no
  file. Confirmed by reconstructing the full day's session timeline and finding a 4h09m gap
  with no records anywhere, where other evidence independently proves a session was
  actively running.
- A separate, real bug: `classify()` found the last human turn was "answered" because a
  later auto-generated `"Continue from where you left off."` / `"No response requested."`
  stub followed it — masking an earlier real turn that got zero genuine reply. That earlier
  turn turned out to be a **downtime note** (queued while another session was dead — a
  distinct resume-interrupted feature), not the primary session to resume. Conflating the
  two led to misidentifying the wrong session as "the one to resume" mid-investigation.

Full investigation trail: see the `transcript-flush-lag-investigation` and
`llm-gateway-budget-limit` memory files (auto-memory, not in this repo) for the evidence.

## Pass split (added after Opus review, 2026-07-18)

An Opus review of the original single-pass plan found two real problems: (1) the
"reuse existing downtime-note markers" premise below was FALSE — no stub-detection markers
exist anywhere in this codebase, so item 1 requires inventing stub detection from scratch; and
(2) the "(Ø) no file at all" idea (item 2) contradicts an existing documented guarantee in the
module docstring (the zero-file case is currently described as "unrecoverable by design")
and has no firm threshold/placement/false-positive design yet. Recommendation: split into two
passes. Confirmed with the user.

- **Pass A (this plan, ready to implement):** items 1, 3, 4 below.
- **Pass B (tabled, gated on a design decision):** item 2. Do not start until the open
  questions in that section have real answers, not placeholders.

## Scope for Pass A

1. Fix the `classify()` masking bug. **Return-shape decision (Opus-reviewed, both passes of
   review): ADDITIVE, not a breaking replacement.** Keep the existing
   `{interrupted, has_work, dangling, reason}` shape exactly as-is — all 5 current call sites
   (`_recommended`, `_run_list`, `_orphaned_queued_notes`, `_emit_auto`, and `classify()`'s own
   callers) and the ~20 existing test assertions in `tests/test_detect.py` keep working
   unchanged. Add the primary-session/downtime-note distinction as a NEW field or an extended
   `reason` value (e.g. `reason` gains a `'downtime-note'` value, or a new
   `is_downtime_note: bool` alongside the existing fields) — whichever fits more naturally
   once the actual masking-bug fix is being written. Rationale (from the second Opus review):
   the primary-vs-downtime distinction is really a CROSS-session judgment already correctly
   owned by the orchestration layer (`_recommended()` vs `_orphaned_queued_notes()`), not
   something that belongs inside per-session `classify()`'s return type — so don't force it
   into a full `{primary_interrupted, downtime_notes}` redesign. The existing tests encode real
   regression history (the f100ee4c false-positive fix, the 2026-07-15 stale-last-prompt bug)
   and should stay green as an untouched control while the new path gets its own new tests.
   **There is no existing stub-detection code to "reuse"** — the masking-bug fix has to
   recognize the `"Continue from where you left off."` / `"No response requested."` pattern (or
   similar auto-continuation shapes) from scratch; build that as new logic inside `classify()`,
   not by repurposing `_orphaned_queued_notes()` (which solves a different, cross-file problem).
2. Add turn/message count to `--list` output (small addition, inspired by native `/resume`'s
   picker, which shows message count alongside each session).
3. Leave the ~2-4 minute gap-timing heuristic OUT of active detection logic (zero confirmed
   positive examples so far) but leave an inline comment at the natural spot so a future pass
   with real examples can pick it up.
4. No behavioral nudging (e.g. "wait out the countdown next time") — stay purely diagnostic.
5. No change to `--list`'s general diagnostic framing vs. native `/resume` — audited, no
   actual drift found; not a real problem today. (Only borrowing the message-count idea per
   item 2 above — not building a picker.)
6. Bump the plugin version (currently 0.2.13) when this ships, per no-hidden-changes — this is
   a published plugin with real daily usage, and even an additive field change is a real
   behavior change worth a version bump.

Out of scope for Pass A: the "(Ø) no file at all" feature (see Pass B below), and the root
cause of *why* transcripts don't write continuously on this machine (tracked separately —
leading lead is upgrading Claude Code past 2.1.197, tabled until after this plan is saved).

## 1. Fix classify() masking bug (SUPERSEDED — see "Scope for Pass A" item 1 above)

The text originally here proposed a breaking `{primary_interrupted, downtime_notes}` return
shape and claimed downtime-note detection could reuse `_orphaned_queued_notes()`. Both were
wrong per two rounds of Opus review (false-reuse premise; unacknowledged breaking-change blast
radius). Superseded by the additive approach in "Scope for Pass A" item 1. Kept only as a
record of what NOT to do:

- **Current bug (still accurate):** `classify()` finds `last_human_idx` as the LAST
  human-authored turn in the file, then checks whether *that* turn got an assistant reply. An
  auto-generated continuation stub (`"Continue from where you left off."` →
  `"No response requested."`) is itself a human+assistant turn pair, so if it appears after a
  real, never-answered turn, it satisfies the "was answered" check and the real problem
  underneath is never surfaced. Fix direction (still accurate): walk human turns in order and
  identify the EARLIEST never-answered one, not just the last one.
- **Do NOT** replace the return shape wholesale, and do NOT assume `_orphaned_queued_notes()`
  already recognizes this stub pattern — it doesn't; nothing in this codebase does yet.

**Test to add:** a fixture reproducing the exact real-world shape — one early real human turn
with no reply, followed by a later `"Continue from where you left off."` stub with a filler
assistant reply — asserting the fix still surfaces the early turn and correctly marks it as a
downtime note (not the primary session), via whichever additive field/value ends up chosen.

## 2. Pass B (TABLED): detect the "(Ø) no file at all" case

This is fundamentally different from the other three shapes: there's no file to classify, so
it can't live inside `classify()`. It has to work at the directory level, comparing session
boundaries across ALL files in a project.

**Approach:** for the project's transcript directory, sort all session files by their
earliest and latest real timestamp (not mtime — mtime already proved misleading once this
session). Walk consecutive sessions in time order and flag a gap when:

- the gap between one session's last real timestamp and the next session's first real
  timestamp exceeds some threshold (needs a number — start conservative, e.g. 20-30 minutes,
  tunable), AND
- there's independent reason to believe real activity happened in that window (this is the
  hard part — in the confirmed 2026-07-18 case, the only reason we knew activity happened was
  a *memory file* timestamped inside the gap, which isn't something the detector can see).

**Open design question to resolve BEFORE Pass A is even considered done (not "during
implementation" — this needs answers up front now, per the second Opus review):**

1. Without an external timestamp oracle (like a memory file), can the detector distinguish
   "user was just away from the keyboard for 4 hours" from "a session ran and left zero
   trace" at all? Honest answer: probably not with certainty. The realistic version of this
   feature is: flag ALL gaps above the threshold as "possible lost session, time range
   HH:MM–HH:MM, unconfirmed" — never claim certainty, accept some false positives (ordinary
   breaks) in exchange for never silently missing a real one. Report the gap as INFORMATION,
   not a diagnosis.
2. Exact threshold — not yet decided (20-30 min was a first guess, unvalidated).
3. Where this lives — `--list`-only (surfaced on demand) vs. an automatic SessionStart
   banner (surfaced unprompted). These have very different false-positive tolerance:
   automatic banners need a much higher threshold/confidence than an on-demand `--list` note.
4. **Must reconcile with the module docstring**, which (per the second Opus review)
   currently states the zero-file case is "unrecoverable by design; there is nothing for this
   script's logic to detect." Rewriting that guarantee needs to be an explicit, visible part
   of whatever change ships this — not a silent contradiction.

Do not start coding Pass B until 1-4 above have real answers.

## 3. --list: add turn/message count

**File:** `hooks/detect-interrupted.py`, `_run_list` (~line 478) and `classify()`.

`classify()` already computes `work` (a count) internally but only returns a boolean
`has_work`. Return the raw count instead (or alongside), and print it in `--list`'s per-row
output next to the existing date/tag/classification/dangling-prompt fields. Small, additive,
no behavior change to detection logic.

## 4. Gap-timing heuristic — comment only, no logic

At the point in `classify()` (or wherever the "(L) lost tail" / trailing-marker detection
ends up living per item 2) where the trailing content-free marker is identified, add a
one-line comment noting: the gap between last real content and that marker's timestamp is
worth comparing to the ~3-minute retry-exhaustion window IF a future case ever shows it in
that range — no such case has been observed yet (checked 12 real trailing-marker examples,
2026-07-18, all under 75 seconds), so no logic should be built on it until one does.

## Testing plan

- Extend `tests/test_detect.py` with fixtures for: the masking bug (item 1), a directory-level
  gap scenario (item 2 — probably needs a new test file/harness since it's not single-file
  `classify()` logic), and the `--list` turn-count output (item 3).
- Manual smoke test: run `--list` against this machine's real transcript directory and
  confirm the 2026-07-17 `c8605b53` case now correctly reports as a downtime note, not a
  primary interrupted session.

## Explicitly not doing in this pass

- Not touching the root-cause investigation into why transcripts don't flush continuously.
- Not adding any UI/behavior that resembles a `/resume`-style session picker.
- Not adding the countdown-wait nudge.
- Not implementing the gap-timing heuristic as active logic.

## Next steps after this plan is saved

1. Opus review pass on this plan document (confined job — catch gaps/scope creep before
   implementation starts).
2. Session wrap-up.
3. Separately (not blocking this plan): try `brew upgrade --cask claude-code` with the
   backup/rollback path already prepared, to see if it affects the flush-lag root cause.
