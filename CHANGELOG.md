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

**Tags.** Annotated tags exist from ``v0.3.0`` onward, each on the commit that set `plugin.json` to
that version. Earlier versions are deliberately untagged: they are early-development releases nobody
would cite.

## [Unreleased]

_Nothing yet._

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
