# resume-interrupted

A Claude Code plugin that notices when your **most recent session was cut off mid-task**
and proactively offers to pick up where you left off.

When a session dies on a usage/credit limit, a crash, or a dropped connection, **no
compute happens afterward** — so nothing can record that the work was unfinished. The
interruption is invisible to your next session, and the task quietly falls through the
cracks. This plugin recovers it from the transcript.

## What it does

- A **SessionStart hook** inspects your most recent *substantive* prior session in the
  current project. If it was interrupted, it shows a **user-visible banner** *and* gives
  Claude the context to offer to resume — naming the likely dangling request.
- A bundled **skill** (`resume-interrupted`) carries the recovery procedure: locate the
  transcript, read its tail, reconstruct intent, and continue — invoked on the notice or
  whenever you say "continue" / "pick up where we left off".
- **Browse mode:** run `interrupted` (or ask Claude to "list interrupted sessions") to see
  *every* unresumed session — most recent first, recommendation marked — so you can jump
  back into any of them, not just the last.

### Re-offering vs. nagging

The auto-offer is **not** a one-shot. It re-appears each startup while your most recent
substantive session is interrupted — so if the session that *made* the offer is itself
killed before you reply, you'll be reminded again. It goes quiet only once you've had a
clean substantive session (you've moved on). Use browse mode to revisit anything later.

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

## A limit kill expires; a crash doesn't

`(E)` and `(S)` differ in something that outlives the interruption. A crash tells you nothing
about the world now. A **usage/quota kill** says "you were out of allowance" — true when the
session died, and false again after the provider's next reset. So on resuming a limit kill,
the notice also states whether that constraint is **still current**:

```text
⚡️ INTERRUPTED SESSION — likely unfinished work
Last session (2026-08-13 23:52) was cut off by a usage/API limit mid-task.
Last request: "build the thing"
↻ A limit reset has passed since then — that limit is no longer current.
```

Claude is told, in the same breath, not to warn you about that limit or quietly scale work
down because of it. Without this, yesterday's exhausted allowance gets carried into today as
confident, wrong caution.

- Scoped to `(E)` only — a crash or dropped connection gets no such inference.
- **No allowance value is read, stored, or invented**, and none is assumed to exist: if you
  are never limited you never get a limit kill, so this never fires for you.
- Compares **reset periods, not calendar dates**, so a kill at 23:50 and a resume at 00:05
  correctly count as the same period — nothing has reset yet.
- Kill time comes from the transcript's own last timestamp where present, falling back to the
  file's mtime, and the notice says **which** — the day boundary is exactly where those two
  can disagree.

| Variable | Default | Meaning |
|---|---|---|
| `RESUME_INTERRUPTED_RESET_UTC_HOUR` | `0` | Hour (UTC) the provider's allowance resets |
| `RESUME_INTERRUPTED_RESET_PROPAGATION_MIN` | `10` | Minutes of propagation lag after that hour, part of the boundary rather than an error bar around it |

## Relationship to waypoints

A distinct companion to [`waypoints`](https://github.com/haiggoh/waypoints): separate plugin,
separate store, separate banner label; no code-level dependency either way. The two answer
different questions:

- **resume-interrupted** — *"was my last session cut off?"* Recovers an **interrupted** session, and
  **self-denoises** once you've had a clean substantive session.
- **waypoints** — *"what did I leave open that isn't done yet?"* Carries **deliberate** open to-dos
  forward and **persists** each until you mark it done (it never self-denoises).

Use them together: resume-interrupted catches the *accidental* loose thread; waypoints tracks the
*intentional* ones.

**Optional banner ordering:** after deciding whether to print, this hook always writes a
session-scoped "done" flag to `$TMPDIR-or-/tmp/claude-sessionstart-banners/<session_id>.resume-interrupted.done`.
Any other plugin's SessionStart hook may poll for that file (with its own short, bounded timeout)
to sequence its own banner after this one — resume-interrupted itself never checks for or waits on
anything from the other side. waypoints does exactly this, so resume-interrupted's banner (when it
has one) reliably lands before waypoints'.

## The `interrupted` command

Browse mode is also a plain command. Claude Code puts an enabled plugin's `bin/` directory
on the Bash tool's `PATH`, so `interrupted` works with **no plugin path, no environment
variables, and no model round-trip** — including from Claude Code's shell mode:

```
! interrupted
```

```
interrupted                        # list interrupted sessions, newest first
interrupted list                   # the same, said explicitly
interrupted recommended            # only the session most worth resuming
interrupted --list                 # compatibility alias for `list`
```

Options:

| Option | What it does |
| --- | --- |
| `--project PATH` | Read the project whose working directory is `PATH` instead of the current one. The transcript-directory encoding is done for you, so pass a real path such as `"$HOME"` — never a hand-built `~/.claude/projects/-Users-…` path. |
| `--dir DIR` | Read an already-encoded transcript directory (diagnostics; `--project` is the friendlier form). |
| `--limit N` / `--page N` | Page by item count. The footer names the total and prints the exact next-page command. |
| `--max-chars N` | End a page before it exceeds `N` characters (default 26000). Because titles and queued notes vary in length, a character budget uses the available space better than a fixed item count. `0` disables it. |
| `--all` | No item and no character limit — for an ordinary terminal or a redirection. |
| `--json` | Complete machine-readable output. **Never paginated**, so it stays a stable contract. |

Three guarantees worth knowing, because each is enforced by a test:

- **The recommendation is computed from the complete set, never from a page** — paginating
  can't move or duplicate the `>` marker.
- **No session is ever silently unreachable.** A page always carries at least one whole
  session; if the character budget is too small to hold even one, the session still ships
  and the output *says* the budget couldn't be honoured rather than overshooting quietly.
- **Read-only throughout.** No transcript and no recovery state is modified.

The `interrupted` command and the plugin's SessionStart hook are the same file in two
modes, split on one rule: **no arguments means hook mode** (the hook is invoked with none,
and reads its JSON from stdin), so anything with arguments is the CLI. `--list` keeps
behaving exactly as it always did.

## Install

This plugin is listed in the `haiggoh` marketplace (hosted in the
`get-haiggoh` repo):

```
/plugin marketplace add haiggoh/get-haiggoh   # or: update haiggoh
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
