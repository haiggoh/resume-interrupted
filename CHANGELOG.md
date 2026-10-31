# Changelog

All notable changes to `resume-interrupted` are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Provenance.** This file was reconstructed on 2026-09-03, after the fact, from the complete
first-parent Git history. Each commit was read together with the release it actually shipped in —
that is, the next version bump at or after it, *not* the version its own message happens to mention,
which is usually the previous release. Release dates are the date of the commit that set
`.claude-plugin/plugin.json` to that version. Entries therefore summarise recorded commits rather
than reproducing notes written at release time.

## [0.8.1] - 2026-10-31 — ⚡️: the recovery-offer context leads with the mark

- The repo's GitHub About emoji (⚡️) now leads the model-facing recovery-offer context — the
  plugin's credit moment (it just recovered a lost session). The user-visible boxed banner already
  carried the ⚡️ caps header upstream (with the VS16 presentation note); the all-clean note stays
  unbranded (idle surface). Plan 2026-10-28, §Wave 3 Task 11 — at 0.8.0 the line had drifted from
  the plan's :881 survey to :907; located fresh.
- The `bin/interrupted` CLI carries no idle banner (it prints data rows), so no CLI surface to
  brand — the plan's "CLI banner" premise decayed; recorded rather than invented.
- A ≤3-line Branding section in the SKILL.md, the plugin.json description, and the README h1 carry
  the mark; tests/test_detect.py pins the offer prefix, the unbranded-prefix absence, and the
  all-clean note's continued plainness.

## [0.8.0] - 2026-10-30

Three detection gaps closed, all measured on real interrupted sessions.

### Added
- **(T) between-turns death shape** (`hooks/detect-interrupted.py`): the last human turn was
  answered and the transcript ends in an `api-request` that never got a response — the
  session died issuing the next request. Measured 2026-10-29 (session 0a39d429, tail
  `tool_result → attachment → api-request-blob → api-request`): every prior shape checks a
  record against its completion, so this death read as "not interrupted" and a
  half-finished two-deliverable request sat unnoticed ~22h. Classified after (S): an
  unanswered prompt followed by its own in-flight request stays a stall; (E) still outranks.
- **`--all-projects`** on `list`/`recommended`: scan every project folder under the projects
  root instead of only the launch dir's — sessions run in project or worktree checkouts
  were invisible from a home launch. Rows (and JSON) carry the project folder and the
  session's custom window title (sidecar `custom-title.json`; missing/corrupt degrades to
  empty, read as utf-8 with replacement so a non-UTF-8 locale never loses a title).
  Subagent transcripts never enter the listing — depth-1 scan plus an explicit guard for
  one symlinked to project level. The scan is bounded to the last 14 days of writes
  (`RESUME_INTERRUPTED_ALL_PROJECTS_WINDOW_S` seconds overrides, `0` disables, invalid is
  a loud error) so an ancient multi-GB store cannot hang it. Scan approach borrowed from
  cc-transcript's cross-project session discovery.
- `RESUME_INTERRUPTED_PROJECTS_ROOT` overrides the projects root (tests, unusual installs);
  set but not a directory is an error for `--all-projects`, never a silent fallback.

### Changed
- The skill's candidate-pick rule: **a session the user names is an instruction, not a
  suggestion** — resume it without re-litigating against the newest interrupted session,
  regardless of verdict; concurrent sessions each resume their own named target instead of
  racing to the same latest one. `not interrupted` is not `nothing to resume`.

## [0.7.0] - 2026-10-30

### Added
- `interrupted tail <session-id-or-path>`: a read-only transcript tail parser. It prints the
  detector's verdict, the last `--records N` conversational records condensed (tool calls and
  results, errors flagged), every unanswered prompt with `queue-operation` records tagged as
  such, any unanswered `tool_use`, and a reset-boundary check. `--json` gives the same as one
  object; `--chars N` clips each record. Replaces the inline python every recovery used to
  hand-roll.
- Soft dependency on audit-loose-ends: when it is installed, `tail` appends its `audit-scan.py`
  digest of the resumed session, run on the explicit transcript path (never `--last`, which
  ranks by mtime). Resolved from `RESUME_INTERRUPTED_AUDIT_SCAN` (set but unusable is an
  error), then `installed_plugins.json`, then the highest NUMERIC cache version. Absent means
  silently skipped; a crashing scanner is reported inside the digest section and never fails the
  tail. `--no-audit` opts out. `RESUME_INTERRUPTED_PLUGINS_DIR` relocates the search.

### Changed
- SKILL.md documents the precedence (E) > (M) > (S) and that a mid-tool or stalled death can
  still be a quota kill when the kill left no error turn; the reset-boundary check is offered
  for every interrupted session by `tail`, labelled with when it applies. Detector logic is
  unchanged: the precedence is now pinned by tests.

## [0.6.0] - 2026-10-26

- **A live session is never offered as a resume candidate** (batch F). The launcher's
  registry (`~/.claude/sessions/<pid>.json`) is consulted: a session is live only on
  positive evidence — a matching entry whose pid is running AND whose recorded `startedAt`
  matches the process (compared via `ps -o etime=`, since `etimes` is Linux-only and the
  `procStart` string can sit a timezone frame away from ps's locale-formatted `lstart`;
  measured live on this machine's DST boundary, 2026-10-25). Live sessions are excluded
  from the recommendation, the orphaned-note walk and the listing.
- **Registry can't answer ⇒ honest hedging**: a transcript written within 10 minutes is
  reported as "may still be running" instead of a confident stalled verdict (`may_be_running`
  in `--json`, a listing marker, and such sessions are not recommended either). An explicit
  `RESUME_INTERRUPTED_SESSIONS_DIR` override that is set but invalid is named on stderr and
  treated as absent for that run — never a silent fallthrough to the default.
- **(M) mid-tool kills**: a session whose last assistant turn emitted a `tool_use` that no
  later `tool_result` answers is now detected — a third structural shape beside (E)/(S).
  Measured 2026-10-25: a 7-hour autonomous session died in exactly this shape, matched
  neither (E) nor (S), and was invisible to every automated resume surface. New banner
  wording; no limit constraint is inferred for it.
- **Queue-record harvest**: `queued_prompts()` also reads `queue-operation` records — a
  prompt typed mid-turn can exist ONLY as such a record (absorbed into the running turn,
  never a user turn), so a user-role scan loses it entirely. Deduped against user turns.
- Tests 174 → 201 (liveness fixtures: live / dead-pid / pid-reused / no-startedAt /
  corrupt-json / invalid-override; mid-tool shapes; queue-record dedup; two mutation
  checks), all in temp dirs — the real registry is never touched.

**Tags.** Annotated tags exist from ``v0.3.0`` onward, each on the commit that set `plugin.json` to
that version. Earlier versions are deliberately untagged: they are early-development releases nobody
would cite.

## [Unreleased]

## [0.5.3] - 2026-10-25

### Fixed
- Removed a stray `_Nothing yet._` line that the 2026-09-03 CHANGELOG reconstruction left
  between the 0.4.1 and 0.4.0 sections. Cosmetic only; no entry changed.

## [0.5.2] - 2026-10-25

### Added
- SKILL.md: how to find the right session when its last prompt is not a good hint — the
  `interrupted` list orders by last prompt, so search transcripts by topic (`grep -l`
  across the session files, then read the forks under the session's `subagents/`
  directory).

## [0.5.1] - 2026-10-25

### Added
- SKILL.md: read recorded history (an interrupted session, a to-do note, a plan) *before*
  starting a task that has any — a prior pass's findings, and the constraints the user
  recorded there, still bind.
- SKILL.md: during a down phase, harvest *every* unanswered user turn queued in the stalled
  session, not just the trailing one — users queue several notes, and the last is not
  necessarily the most valuable.
- SKILL.md: transcripts embed the instruction file that was active when they ran, so old
  transcripts double as an instruction-file version history.
- SKILL.md: a session killed mid-background-workflow leaves a `journal.jsonl` and per-agent
  transcripts that outlive the ephemeral task output — recover completed-but-unsaved
  workflow results from there before concluding the work is lost.
- SKILL.md: session-summary files are not the user's words; read the transcript's own user
  records before attributing a quote.
- `docs/DESIGN.md`: the detection contract, de-dup decisions, independence directive and
  shape rationale, so the plugin's design history is public and durable.

## [0.5.0] - 2026-10-03

### Added
- A `shortcuts` file at the plugin root declares `interrupted`, so `get-haiggoh` (0.8.0 or
  later) can put it on your own shell `PATH` through a version-independent shim. Nothing
  changes for the command itself or for the Bash-tool `PATH` entry Claude Code already adds.

## [0.4.1] - 2026-09-11

### Fixed
- `reason` no longer reports every API-error death as a usage-limit kill. `classify()` returned
  `"limit-kill"` unconditionally whenever the last assistant turn was an error, so a dropped wifi
  connection told the next session a quota had been hit — feeding the exact budget misread this
  plugin's own resume notice is meant to prevent. The reason now names what actually happened:
  `limit-kill` (a 429 quota refusal, the only kind that carries a constraint into the next
  session), `connection-drop` (the request never reached the provider — DNS failure, refused or
  reset socket, stalled stream) or `api-error` (reached the provider, which failed or was busy).
  The kind is decided on `apiErrorStatus` first, because a status code is structural and survives
  a change of error wording; the message text is consulted only when no status was recorded.
- The reset-staleness inference stays scoped to a real quota kill, so a connection drop no longer
  produces an "a limit reset has passed" line about a limit that was never reached. The banner and
  the model-facing context both name the real cause instead of the generic "usage-limit/API error".

### Notes
- Measured against 118 real quota kills and 15 transport deaths in live transcripts: every quota
  refusal carried `apiErrorStatus=429` and every transport death carried no status at all, so the
  status field alone separates them. Adding `ENOTFOUND` to `ERROR_SIGNATURES` — the obvious-looking
  fix — would have been dead code: those turns are already detected structurally.
- 16 new assertions (174 total), mutation-tested 5/5.

## [0.4.0] — 2026-08-31

### Added
- `interrupted` CLI command, reachable directly from the Bash tool with no plugin path, `CLAUDE_PLUGIN_ROOT`, or model round-trip. The wrapper defaults to **browse** mode on empty argv (it supplies `--list`), whereas the underlying hook script still defaults to SessionStart-hook mode and waits on stdin — so the same detector serves both callers without either having to pass a flag.
- Argument surface for browse mode: `list`/`recommended`, `--project PATH` (real transcript path, encoding handled for you), `--dir DIR` (already-encoded), `--limit`/`--page`/`--max-chars` (page by items *and* characters), `--all`, `--json`, `--help`.

### Changed
- `SKILL.md` now instructs `interrupted` first; the old path-construction advice is demoted to a not-found fallback.
- CLI mode no longer swallows exceptions (the hook path still does, so a traceback can never land in a session start).

## [0.3.1] — 2026-08-17

### Fixed
- Banner header now uses the session's own event time, not the file's mtime, so the header and the kill-time line agree on date and timezone.

## [0.3.0] — 2026-08-14

### Added
- On resume, a usage/quota (limit) kill now reports whether the constraint is still current, comparing reset *periods* rather than calendar dates so provider propagation lag is part of the boundary.
- Boundary configurable via `RESUME_INTERRUPTED_RESET_UTC_HOUR` and `RESUME_INTERRUPTED_RESET_PROPAGATION_MIN`.

### Fixed
- Limit-kill resume no longer carries the dead "out of allowance" constraint forward as a confident, wrong caution once the provider's reset has passed.

## [0.2.17] — 2026-07-25

### Added
- On resume, review a "secondary transcript store" — sessions run through a proxy/relay/alternate client that don't persist a native transcript in `~/.claude/projects` — and correct any bad state left there.

## [0.2.16] — 2026-07-24

### Added
- `--list` now shows a per-row work-turn count and a downtime-note flag.

### Fixed
- `classify()` now skips Claude Code auto-continuation stubs ("Continue from where you left off." / "No response requested.") when finding the last genuine human turn, so an earlier real never-answered turn is no longer masked by a later stub's filler reply.

## [0.2.15] — 2026-07-19

### Fixed
- `--list` command now runs from the Bash tool: the old `$CLAUDE_PLUGIN_ROOT` path is empty outside a hook, so it failed with "No such file". Replaced with a Bash-tool-safe path built from the skill's base directory, with a marketplace-agnostic fallback that globs the newest installed copy under `~/.claude/plugins/cache`.

## [0.2.14] — 2026-07-18

### Changed
- Queued-note content now appears directly in the interrupted-session banner (previously only a count), using the same word-boundary-safe extraction as the model-facing context.

### Fixed
- Restored README content and corrected the marketplace-add instructions (`claude-code-desktop-sync` → `get-haiggoh`).

## [0.2.13] — 2026-07-17

### Fixed
- Orphaned-queued-notes walk now scans sessions *newer* than the clean top-of-stack session (the observed production failure mode), stopping at the first session with real work. Recency window is anchored to wall-clock "now" rather than the clean session's mtime.

## [0.2.12] — 2026-07-17

### Added
- When the full resume offer is suppressed, a lightweight one-shot notice surfaces orphaned queued notes a user typed into an earlier interrupted session during a down phase (e.g. "I fixed that bug and pushed it") that never got a reply.

### Fixed
- Documented that a sufficiently abrupt kill can leave zero transcript trace and is unrecoverable by design.

## [0.2.11] — 2026-07-16

### Added
- If `no-hidden-changes` is installed and enabled, briefly poll its done-flag before deciding this hook's own banner, so banners land in a predictable order.

### Changed
- Recovery procedure in `SKILL.md` now reads back at least 3 real turns before the interruption point, since a single trailing message can be a queued note sent while an earlier action was still in flight.

## [0.2.10] — 2026-07-15

### Fixed
- Stale last-prompt marker no longer causes false-positive stalled detection: after a prompt like "wrap" gets answered (even via a skill round-trip), the marker's echo no longer masks an already-answered session as freshly stalled.

## [0.2.9] — 2026-07-15

### Added
- Optional one-way cross-plugin banner-order signal: writes a session-scoped "done" flag on exit so any other plugin's `SessionStart` hook may optionally poll it to sequence its banner after this one.

## [0.2.8] — 2026-07-15

### Fixed
- ⚡ emoji now renders as a colour emoji instead of a monochrome glyph (added explicit VS16 suffix).

## [0.2.7] — 2026-07-14

### Added
- Boxed WARNING banner ("⚡ INTERRUPTED SESSION" header, rules, action footer) and harvest of all queued down-phase notes surfaced in `--list` and the model-facing context.

## [0.2.6] — 2026-07-13

### Changed
- Interruption banner now prefixed with a ⚡ high-voltage emoji as a prominent at-a-glance alarm signal.

## [0.2.5] — 2026-07-12

### Changed
- Docs note the reciprocal relationship with the waypoints plugin.

## [0.2.4] — 2026-07-10

### Changed
- Banner prompt-quote cap raised from 70 to ~100 characters; word-boundary + ellipsis logic unchanged.

## [0.2.3] — 2026-07-10

### Changed
- Banner wording is now reason-aware: a limit-kill reads "cut off by a usage/API limit mid-task" while a stall reads "left a request unanswered". Prompt quotes are trimmed on a word boundary and marked with a single ellipsis, fixing mid-word cuts.

## [0.2.2] — 2026-07-10

### Changed
- Limit-kill detection now uses the transcript's structural markers (`isApiErrorMessage` / `apiErrorStatus`) before falling back to anchored error-text matching, surviving error-wording changes and covering error kinds never enumerated.

## [0.2.1] — 2026-07-09

### Fixed
- Limit-kill detection no longer misfires when a healthy turn merely quotes an error phrase: switched to the transcript's structural marker with an anchored `startswith()` fallback, and counting a turn that quotes an error phrase as real work.

## [0.2.0] — 2026-07-09

### Added
- User-visible banner on detection (alongside the model context) and `--list` mode listing all interrupted sessions, including bare probes, with the recommended substantive candidate marked.

### Changed
- Skill gains a browse-and-select flow with a probe-transparency caveat.

## [0.1.0] — 2026-07-09

### Added
- Detects an interrupted prior session (API/limit kill or stalled tail) on the most recent substantive session and offers to resume, via a `SessionStart` hook and skill. Stdlib-only, never blocks a session.
