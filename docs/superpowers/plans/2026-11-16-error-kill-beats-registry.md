# Plan 2026-11-16: terminal API-error kill outranks the live-registry filter (resume-interrupted 0.8.4)

## Authority
User-directed in session 972c0262 (2026-11-16 ~16:00 CET), four requirements:
1. `/resume-interrupted` invoked alone must surface the two most recent budget-killed sessions
   (`a483fbfd` — plans/motion-pipeline work, and `d75e7528` — ClaudeWorkspace repo work; both
   died 2026-11-16 13:47 UTC on `422 Budget has been exceeded`, Joyia-Code-M4m $40 cap).
2. Sessions that are alive AND making progress must never be candidates — verify the filter.
3. Recency wins once filtered for "actually interrupted".
4. Offering the most likely candidates via AskUserQuestion becomes part of the SKILL.md routine.
Ship depth: commit on a feature branch, push, merge to main, tag `v0.8.4`, GitHub release,
refresh installed plugin, verify visibility + dogfood (`interrupted recommended --all-projects`
must list `a483fbfd` first). NO force-push, no moving published tags.

## Root cause (verified, not assumed)
`hooks/detect-interrupted.py` checks `session_live()` BEFORE `classify()` at three sites
(`_recommended` ~L755, `_orphaned_queued_notes` ~L834, `_collect_rows` ~L1218) and in
`_run_tail` ~L1511. The registry `~/.claude/sessions/<pid>.json` still lists pids 9233/6594 as
alive (ps state `Ss+`, NOT zombies — the launcher process outlives the budget-killed API
session), so both budget-killed sessions are skipped as "LIVE" and never listed/recommended.
`classify()` on their transcripts returns `interrupted=True, reason=limit-kill` (last turn is
`isApiErrorMessage` 422) — positive evidence the SESSION is dead.

## Fix
- New module constant `_ERROR_KILL_REASONS = ("limit-kill", "api-error", "connection-drop")` +
  helper `_terminal_kill(info)` = interrupted AND reason in that set (the (E) death branch of
  `classify` is the only source of those reasons).
- All three filter sites + tail: classify FIRST; skip as live only when
  `session_live(sid) and not _terminal_kill(info)`. Ambiguous tails (stalled/mid-tool/
  between-turns/clean) still defer to the registry — a live, progressing session has no
  terminal error turn, so it stays excluded (requirement 2 preserved).
- `_run_tail`: `live = session_live(sid) and not _terminal_kill(info)`; when the registry pid
  is alive but the tail is a terminal kill, print "registry process alive, but the transcript
  ended in a terminal API error — the session is dead" instead of "LIVE session -- do not
  double-drive".
- Recency (requirement 3): `_collect_rows` already sorts newest-first and recommends the first
  substantive non-maybe-running interrupted row; with the precedence fix the newest kill
  (`a483fbfd`, mtime 16:22) wins. `recommended` already supports `--all-projects` via
  `_collect_rows(None, all_projects=True, ...)` — no CLI change needed; SKILL.md will use it.
- SKILL.md (requirement 4): listing section — replace "present as a short table and ask" with
  a routine AskUserQuestion step: recommended row first labelled "(Recommended)", plus the next
  up to 3 substantive `[work]` rows from `interrupted --all-projects`, option description =
  project + reason + dangling prompt; fall back to prose only if the user declines. Document
  the precedence rule (terminal error turn = dead even if the registry pid lives).

## Tests (tests/test_detect.py, new v0.8.4 section, RED before the fix)
- terminal limit-kill transcript + registry entry with MY_PID alive → still listed, still
  RECOMMENDED, `recommended` returns it, tail shows no "do not double-drive" and names the
  terminal-error/registry-alive note.
- guard: stalled tail + live pid → still NOT listed (registry still wins on ambiguous tails).
- recency: two interrupted sessions (kill older, stalled newer, both dead pids) → newer wins.
- `recommended --all-projects` finds a kill in a second project dir under a fake
  RESUME_INTERRUPTED_PROJECTS_ROOT while the cwd project has none.
- SKILL.md source contains "AskUserQuestion" (routine step pinned).
Run: `python3 tests/test_detect.py && python3 tests/test_shortcuts.py && python3 tests/test_portable.py`.

## Version surfaces + ship
0.8.3 → 0.8.4 in `.claude-plugin/plugin.json`, CHANGELOG.md top entry, then
`python3 scripts/build-portable.py` regenerates `portable/` (manifest, PORTABLE-BUILD.json,
dist zip). Verify no personal paths in portable (build-portable handles it; test_portable pins).
Commit by path on the branch with BOTH trailers:
`Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` +
`Co-Authored-By: Mistral Large 4 "Le Chonk" 1T-A52B <noreply@mistral.ai>` (this session's running
model per [[commit-attribution-by-model]]). Ship with `rtc-ship.sh` from the worktree
(rtc-ship.json: manifest .claude-plugin/plugin.json, tag prefix v, default branch main,
refresh_plugin resume-interrupted). After tag: verify visibility
(`git ls-remote --tags origin` + get-haiggoh plan / hooks/check-tag-visibility.py), then
`claude plugin update` (or get-haiggoh) and dogfood: `interrupted recommended --all-projects`
lists a483fbfd first. Release notes = CHANGELOG section verbatim, NO 🤖 trailer.

## After the ship (same session, separate step)
Resume the two budget-killed sessions EACH SEPARATELY via free-session-handover
(free lane — the Joyia $40 cap is still in force until 2026-11-17 00:10 UTC; free lane is
unaffected): handoff doc per session, launch via free-agents `bin/launch-remote-window.sh`
in a real Terminal window (sudo-in-terminal.sh wrapper), verify registration + handover receipt.

## Known-bad discriminants (mutation checks)
- Reverting the precedence (live-check first) must turn the new list/recommended tests RED.
- Making `_terminal_kill` always True must turn the stalled-live guard test RED.
