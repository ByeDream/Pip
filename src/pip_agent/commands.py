"""
Unified slash-command dispatch for all channels.

Commands are intercepted before routing and agent_loop. Each handler
receives the current message context and returns a response string
(or None to signal no reply needed).

All commands are flat (single-level):
  /help, /bind, /name, /unbind, /clear, /status, /exit
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pip_agent.channels import InboundMessage
from pip_agent.routing import (
    AgentConfig,
    AgentRegistry,
    Binding,
    BindingTable,
    build_session_key,
    normalize_agent_id,
    resolve_effective_config,
)


@dataclass
class CommandContext:
    inbound: InboundMessage
    registry: AgentRegistry
    bindings: BindingTable
    bindings_path: Any  # Path
    workdir: str = ""


@dataclass
class CommandResult:
    handled: bool
    response: str | None = None
    exit_requested: bool = False


_AT_MENTION_RE = re.compile(r"^(?:@\S*\s+)+")


def dispatch_command(ctx: CommandContext) -> CommandResult:
    """Parse and dispatch a slash command. Returns CommandResult."""
    text = ctx.inbound.text.strip()
    text = _AT_MENTION_RE.sub("", text).strip()
    if not text.startswith("/"):
        return CommandResult(handled=False)

    parts = text.split(None, 1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    handlers = {
        "/help": _cmd_help,
        "/bind": _cmd_bind,
        "/name": _cmd_name,
        "/unbind": _cmd_unbind,
        "/clear": _cmd_clear,
        "/status": _cmd_status,
        "/exit": _cmd_exit,
    }

    handler = handlers.get(cmd)
    if handler is None:
        return CommandResult(handled=False)

    return handler(ctx, args)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

_HELP_TEXT = """\
Available commands:

  /help                          Show this help
  /bind <agent-id> [options]     Bind current chat to an agent (auto-creates if needed)
  /name <display_name>           Set display name for the current agent
  /unbind                        Remove current chat's routing binding
  /clear                         Remove binding and delete the agent config
  /status                        Show current routing info
  /exit                          Quit Pip-Boy (CLI only)

/bind options:
  --scope <dm_scope>             Session isolation (per-guild, per-guild-peer, main)
  --model <model>                Override model
  --max-tokens <n>               Override max tokens
  --compact-threshold <n>        Override compact threshold
  --compact-micro-age <n>        Override compact micro age

In a group chat, /bind creates a guild-level (T2) binding.
In a private chat, /bind creates a peer-level (T1) binding.
Bindings persist across restarts in .pip/agents/bindings.json."""


def _cmd_help(ctx: CommandContext, args: str) -> CommandResult:
    return CommandResult(handled=True, response=_HELP_TEXT)


# ---------------------------------------------------------------------------
# /bind
# ---------------------------------------------------------------------------

def _persist_agent_md(cfg: AgentConfig, agents_dir: Path | None) -> None:
    """Write an AgentConfig to its .md file."""
    if not agents_dir:
        return
    agents_dir.mkdir(parents=True, exist_ok=True)
    md_path = agents_dir / f"{cfg.id}.md"
    frontmatter = (
        f"---\n"
        f"name: {cfg.name}\n"
        f"model: {cfg.effective_model}\n"
        f"max_tokens: {cfg.effective_max_tokens}\n"
        f"dm_scope: {cfg.effective_dm_scope}\n"
        f"compact_threshold: {cfg.effective_compact_threshold}\n"
        f"compact_micro_age: {cfg.effective_compact_micro_age}\n"
        f"---\n"
    )
    body = cfg.system_body or ""
    md_path.write_text(frontmatter + body + "\n", encoding="utf-8")


def _auto_create_agent(
    registry: AgentRegistry, agent_id: str,
) -> tuple[AgentConfig | None, str | None]:
    """Clone the default agent with a new id/name, persist to disk, register."""
    from dataclasses import replace

    agents_dir = registry.agents_dir
    if not agents_dir:
        return None, f"Agent '{agent_id}' not found and agents directory is not configured."

    default = registry.default_agent()
    body = "Your working directory is {workdir}.\nRead AGENTS.md in your working directory before starting work."
    cfg = replace(default, id=agent_id, name=agent_id, system_body=body)

    _persist_agent_md(cfg, agents_dir)
    registry.register_agent(cfg)
    return cfg, None


def _cmd_bind(ctx: CommandContext, args: str) -> CommandResult:
    if not args.strip():
        return CommandResult(handled=True, response="Usage: /bind <agent-id> [options]\nType /help for details.")

    try:
        tokens = shlex.split(args)
    except ValueError as exc:
        return CommandResult(handled=True, response=f"Parse error: {exc}")

    agent_id_raw = tokens[0]
    agent_id = normalize_agent_id(agent_id_raw)
    agent = ctx.registry.get_agent(agent_id)
    created_new = False
    if agent is None:
        agent, err = _auto_create_agent(ctx.registry, agent_id)
        if err:
            return CommandResult(handled=True, response=err)
        created_new = True

    _KNOWN_FLAGS = {"scope", "model", "max_tokens", "compact_threshold", "compact_micro_age"}

    overrides: dict[str, Any] = {}
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            key = tok.lstrip("-").replace("-", "_")
            if key not in {k.replace("-", "_") for k in _KNOWN_FLAGS}:
                return CommandResult(handled=True, response=f"Unknown option: {tok}")
            if i + 1 >= len(tokens):
                return CommandResult(handled=True, response=f"Missing value for {tok}")
            overrides[key] = tokens[i + 1]
            i += 2
        else:
            return CommandResult(handled=True, response=f"Unknown option: {tok}")

    inbound = ctx.inbound
    if inbound.is_group and inbound.guild_id:
        tier, match_key, match_value = 2, "guild_id", inbound.guild_id
    else:
        tier, match_key, match_value = 1, "peer_id", inbound.peer_id

    ctx.bindings.remove(match_key, match_value)

    binding = Binding(
        agent_id=agent_id,
        tier=tier,
        match_key=match_key,
        match_value=match_value,
        overrides=overrides,
    )
    ctx.bindings.add(binding)
    ctx.bindings.save(ctx.bindings_path)

    effective = resolve_effective_config(agent, binding)
    display_name = agent.name or agent.id
    scope = effective.effective_dm_scope
    model = effective.effective_model

    lines: list[str] = []
    if created_new:
        agents_dir = ctx.registry.agents_dir
        lines.append(f"Created new agent '{agent_id}' (cloned from default)")
        if agents_dir:
            lines.append(f"  config: {agents_dir / (agent_id + '.md')}")
    lines.extend([
        f"Bound to **{display_name}** ({agent_id})",
        f"  scope: {scope} | model: {model}",
        f"  binding: {binding.display()}",
    ])
    return CommandResult(handled=True, response="\n".join(lines))


# ---------------------------------------------------------------------------
# /name
# ---------------------------------------------------------------------------

def _cmd_name(ctx: CommandContext, args: str) -> CommandResult:
    new_name = args.strip()
    if not new_name:
        return CommandResult(handled=True, response="Usage: /name <display_name>")

    inbound = ctx.inbound
    agent_id, _ = ctx.bindings.resolve(
        channel=inbound.channel,
        account_id=inbound.account_id,
        guild_id=inbound.guild_id,
        peer_id=inbound.peer_id,
    )
    if not agent_id:
        agent_id = ctx.registry.default_agent().id

    agent = ctx.registry.get_agent(agent_id)
    if not agent:
        return CommandResult(handled=True, response="No agent found for this context.")

    from dataclasses import replace as _replace
    agent = _replace(agent, name=new_name)
    ctx.registry.register_agent(agent)
    _persist_agent_md(agent, ctx.registry.agents_dir)

    return CommandResult(
        handled=True,
        response=f"Agent '{agent_id}' renamed to **{new_name}**.",
    )


# ---------------------------------------------------------------------------
# /unbind
# ---------------------------------------------------------------------------

def _cmd_unbind(ctx: CommandContext, args: str) -> CommandResult:
    inbound = ctx.inbound
    if inbound.is_group and inbound.guild_id:
        removed = ctx.bindings.remove("guild_id", inbound.guild_id)
    else:
        removed = ctx.bindings.remove("peer_id", inbound.peer_id)

    if removed:
        ctx.bindings.save(ctx.bindings_path)
        return CommandResult(handled=True, response="Binding removed. Falling back to default agent.")
    return CommandResult(handled=True, response="No binding found for this context.")


# ---------------------------------------------------------------------------
# /clear
# ---------------------------------------------------------------------------

def _cmd_clear(ctx: CommandContext, args: str) -> CommandResult:
    """Remove current chat's binding and delete its bound agent (except default)."""
    inbound = ctx.inbound
    if inbound.is_group and inbound.guild_id:
        match_key, match_value = "guild_id", inbound.guild_id
    else:
        match_key, match_value = "peer_id", inbound.peer_id

    agent_id, _ = ctx.bindings.resolve(
        channel=inbound.channel,
        account_id=inbound.account_id,
        guild_id=inbound.guild_id,
        peer_id=inbound.peer_id,
    )

    removed_binding = ctx.bindings.remove(match_key, match_value)
    if removed_binding:
        ctx.bindings.save(ctx.bindings_path)

    lines: list[str] = []
    if removed_binding:
        lines.append("Binding removed.")
    else:
        lines.append("No binding found for this context.")

    if agent_id:
        deleted = ctx.registry.remove_agent(agent_id, delete_file=True)
        if deleted:
            lines.append(f"Agent '{agent_id}' deleted.")
        else:
            lines.append(f"Agent '{agent_id}' is the default and cannot be deleted.")

    lines.append("Falling back to default agent.")
    return CommandResult(handled=True, response="\n".join(lines))


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

def _cmd_status(ctx: CommandContext, args: str) -> CommandResult:
    inbound = ctx.inbound
    agent_id, binding = ctx.bindings.resolve(
        channel=inbound.channel,
        account_id=inbound.account_id,
        guild_id=inbound.guild_id,
        peer_id=inbound.peer_id,
    )
    if not agent_id:
        agent_id = ctx.registry.default_agent().id
        binding = None

    agent = ctx.registry.get_agent(agent_id)
    if not agent:
        agent = ctx.registry.default_agent()

    effective = resolve_effective_config(agent, binding)
    sk = build_session_key(
        agent_id=effective.id,
        channel=inbound.channel,
        peer_id=inbound.peer_id,
        guild_id=inbound.guild_id,
        is_group=inbound.is_group,
        dm_scope=effective.effective_dm_scope,
    )

    lines = [
        f"Agent: {agent.name or agent.id} ({agent.id})",
        f"Model: {effective.effective_model}",
        f"Scope: {effective.effective_dm_scope}",
        f"Session: {sk}",
        f"Channel: {inbound.channel}",
    ]
    if binding:
        lines.append(f"Binding: {binding.display()}")
    else:
        lines.append("Binding: (none — using default)")

    if inbound.is_group:
        lines.append(f"Guild: {inbound.guild_id}")
    lines.append(f"Peer: {inbound.peer_id}")

    return CommandResult(handled=True, response="\n".join(lines))


# ---------------------------------------------------------------------------
# /exit
# ---------------------------------------------------------------------------

def _cmd_exit(ctx: CommandContext, args: str) -> CommandResult:
    if ctx.inbound.channel != "cli":
        return CommandResult(
            handled=True,
            response="/exit is only available in the CLI channel.",
        )
    return CommandResult(handled=True, exit_requested=True)
