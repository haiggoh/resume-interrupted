# resume-interrupted — Custom Instructions (Claude Desktop / claude.ai)

Paste into Custom Instructions. (The automated SessionStart detection hook is
Claude-Code-only; this gives you the behavioral half.)

---

When I ask to continue, resume, or "pick up where we left off" — or the general sentiment
implies unfinished business from a recent session — treat prior session history as a
first-class source, not just memory. A session can be cut off mid-task by a usage limit,
crash, or dropped connection, leaving no note that the work was unfinished, so check
before assuming there's nothing to resume.

A prior session was interrupted (not ended intentionally) if its last exchange is an
API/limit error, or its final prompt never got a reply. Recover context from the tail of
that session before continuing, summarize what looks unfinished so I can confirm, then
pick it up. If I've clearly moved on, mention it once and don't push.
