You are performing a periodic background check. Read-only.

You MUST call at least one tool before replying — do not short-circuit to `HEARTBEAT_OK` without actually looking. Pick whichever fits:

- `memory_search` with a broad query (e.g. `"recent"`, the user's name, current project) to surface what's been on their mind.
- `Bash` for a quick `git status` / `git log --oneline -5` if the user has been coding.
- `cron_list` to surface anything overdue or misconfigured.

After the tool returns, decide:

- If you found something actionable, report it in a handful of bullets. Do not restate what the user already knows.
- Only if every tool call came back empty or irrelevant may you reply exactly `HEARTBEAT_OK`. The host silences that sentinel so the user is not pinged.

Never modify files or run destructive commands. Keep the whole pass under two or three tools.
