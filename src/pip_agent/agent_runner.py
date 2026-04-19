"""SDK-native agent runner: wraps ``claude_agent_sdk.query()`` for Pip-Boy.

This module replaces the hand-rolled ``agent_loop()`` with the Claude Agent SDK.
The SDK manages the full agent loop — tool dispatch, context compaction, and
session persistence — while Pip-Boy's unique capabilities are provided via
an in-process MCP server.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    query,
)

from pip_agent.mcp_tools import McpContext, build_mcp_server

log = logging.getLogger(__name__)


@dataclass
class QueryResult:
    """Return value from :func:`run_query`."""

    text: str | None = None
    session_id: str | None = None
    error: str | None = None
    cost_usd: float | None = None
    num_turns: int = 0


# SDK built-in tools we allow the agent to use.
_BUILTIN_TOOLS = [
    "Bash", "Read", "Write", "Edit", "MultiEdit",
    "Glob", "Grep",
    "WebSearch", "WebFetch",
    "Task", "TodoWrite",
    "mcp__pip__*",
]


def _build_env() -> dict[str, str]:
    """Collect environment variables to pass to the CLI subprocess."""
    env: dict[str, str] = {}
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "SEARCH_API_KEY"):
        val = os.environ.get(key)
        if val:
            env[key] = val
    return env


async def run_query(
    prompt: str,
    *,
    mcp_ctx: McpContext,
    model: str = "",
    session_id: str | None = None,
    system_prompt_append: str = "",
    cwd: str | Path | None = None,
    verbose: bool = False,
) -> QueryResult:
    """Run a single agent turn via the Claude Agent SDK.

    Parameters
    ----------
    prompt:
        The user message to send.
    mcp_ctx:
        Pre-configured MCP context with all runtime services.
    model:
        Model identifier (e.g. ``claude-sonnet-4-6``).
    session_id:
        SDK session ID to resume.  ``None`` starts a new session.
    system_prompt_append:
        Custom text appended to the ``claude_code`` preset system prompt.
        Carries the agent persona, memory enrichment, and skill catalog.
    cwd:
        Working directory for the agent.
    verbose:
        Print intermediate messages to stdout.
    """
    mcp_server = build_mcp_server(mcp_ctx)
    effective_cwd = str(cwd) if cwd else str(mcp_ctx.workdir)

    options = ClaudeAgentOptions(
        model=model or None,
        cwd=effective_cwd,
        resume=session_id,
        system_prompt=(
            {
                "type": "preset",
                "preset": "claude_code",
                "append": system_prompt_append,
            }
            if system_prompt_append
            else None
        ),
        allowed_tools=_BUILTIN_TOOLS,
        permission_mode="bypassPermissions",
        setting_sources=["project", "user"],
        env=_build_env(),
        mcp_servers={"pip": mcp_server},
    )

    result = QueryResult()

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and verbose:
                        print(block.text, end="", flush=True)

            elif isinstance(message, SystemMessage):
                if message.subtype == "init":
                    result.session_id = message.data.get("session_id")
                    if verbose:
                        log.info("Session: %s", result.session_id)

            elif isinstance(message, ResultMessage):
                result.text = message.result
                result.session_id = message.session_id
                result.cost_usd = message.total_cost_usd
                result.num_turns = message.num_turns
                if message.is_error:
                    result.error = message.result
                if verbose:
                    log.info(
                        "Done: turns=%d cost=$%.4f stop=%s",
                        message.num_turns,
                        message.total_cost_usd or 0,
                        message.stop_reason,
                    )

    except ClaudeSDKError as exc:
        result.error = str(exc)
        log.error("SDK error: %s", exc)

    return result
