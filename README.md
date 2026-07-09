# resume-interrupted

A Claude Code plugin that notices when your **most recent session was cut off mid-task**
and proactively offers to pick up where you left off.

When a session dies on a usage/credit limit, a crash, or a dropped connection, **no
compute happens afterward** — so nothing can record that the work was unfinished. The
interruption is invisible to your next session, and the task quietly falls through the
cracks. This plugin recovers it from the transcript.

## What it does

- A **SessionStart hook** inspects your most recent *substantive* prior session in the
  current project. If it was interrupted, it injects a one-line notice so Claude can
  offer to resume — naming the likely dangling request.
- A bundled **skill** (`resume-interrupted`) carries the recovery procedure: locate the
  transcript, read its tail, reconstruct intent, and continue — invoked on the notice or
  whenever you say "continue" / "pick up where we left off".

## How it detects an interruption

A session was interrupted (not ended intentionally) if **either**:

- **(E) Limit / API kill** — its last assistant turn is an API/budget error
  (`Budget has been exceeded`, `API Error: Request rejected`, …).
- **(S) Stalled** — the final human input never got an assistant reply (last prompt
  unanswered, or a prompt that only reached a trailing `last-prompt` marker). Covers
  crashes and dropped connections, not just metered-gateway limits.

It is **self-denoising**: it only considers your most recent session that had *real work*
(≥1 assistant turn), so bare "are we back yet?" availability probes during an outage are
skipped, and once you've completed a clean substantive session it goes quiet.

> The `(E)` budget/credit strings are specific to metered gateways; on a normal setup
> they simply never match and detection falls back to the universal `(S)` structural
> signal. Edit `ERROR_SIGNATURES` in `hooks/detect-interrupted.py` to add your own.

## Install

This plugin is listed in the `haiggoh` marketplace (hosted in the
`claude-code-desktop-sync` repo):

```
/plugin marketplace add haiggoh/claude-code-desktop-sync   # or: update haiggoh
/plugin install resume-interrupted@haiggoh
```

Restart Claude Code so the SessionStart hook loads.

## Requirements

- Claude Code (the SessionStart hook + skill are Claude Code features).
- **Python 3** on `PATH` (standard library only — no pip installs). If `python3` is
  missing the hook no-ops silently; it never blocks a session.

## Safety

- The hook **never blocks a session**: any error → it prints nothing and exits 0.
- It only **reads** transcripts; it writes no state and mutates no files.

## For Claude Desktop / claude.ai

There's no plugin system there. Paste `templates/custom-instructions.md` into your
Custom Instructions to get the resume behavior (the automated detection hook is
Claude-Code-only).

## License

MIT — see [LICENSE](LICENSE). Not affiliated with Anthropic.
