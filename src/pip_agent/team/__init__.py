from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from pip_agent.config import settings
from pip_agent.profiler import Profiler
from pip_agent.tools import WORKDIR, execute_tool

if TYPE_CHECKING:
    import anthropic
    from pip_agent.skills import SkillRegistry

VALID_MSG_TYPES = frozenset({
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
    "plan_request",
    "plan_response",
})

DEFAULT_TOOLS = [
    "bash", "read", "write", "edit", "glob", "web_search", "web_fetch",
]

MAX_TOOL_OUTPUT = 50_000

SEND_SCHEMA = {
    "name": "send",
    "description": (
        "Send a message to a teammate or to 'lead' (the main agent). "
        "Use msg_type='broadcast' to send to all active teammates. "
        "For protocol responses, include req_id and approve."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient name.",
            },
            "content": {
                "type": "string",
                "description": "Message content.",
            },
            "msg_type": {
                "type": "string",
                "enum": sorted(VALID_MSG_TYPES),
                "description": "Message type. Default: message.",
            },
            "req_id": {
                "type": "string",
                "description": "Request ID (for protocol responses).",
            },
            "approve": {
                "type": "boolean",
                "description": "Approve or reject (for protocol responses).",
            },
        },
        "required": ["to", "content"],
    },
}

READ_INBOX_SCHEMA = {
    "name": "read_inbox",
    "description": "Read and drain your inbox. Returns all pending messages.",
    "input_schema": {"type": "object", "properties": {}},
}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, match.group(2).strip()


# ---------------------------------------------------------------------------
# TeammateSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TeammateSpec:
    name: str
    description: str
    model: str
    max_turns: int
    tools: list[str]
    system_body: str

    @classmethod
    def from_file(cls, path: Path) -> TeammateSpec:
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        name = meta.get("name", path.stem)
        description = meta.get("description", "")
        model = meta.get("model", settings.model)
        max_turns = int(meta.get("max_turns", settings.subagent_max_rounds))
        raw_tools = meta.get("tools", DEFAULT_TOOLS)
        if isinstance(raw_tools, str):
            raw_tools = [t.strip() for t in raw_tools.split(",")]
        return cls(
            name=name,
            description=description,
            model=model,
            max_turns=max_turns,
            tools=list(raw_tools),
            system_body=body,
        )


# ---------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------


class Bus:
    """JSONL file-based message bus. Append-only send, drain-on-read."""

    def __init__(self, inbox_dir: Path) -> None:
        self._dir = inbox_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def send(
        self,
        from_name: str,
        to_name: str,
        content: str,
        msg_type: str = "message",
        **extra,
    ) -> str:
        if msg_type not in VALID_MSG_TYPES:
            return (
                f"[error] Invalid msg_type '{msg_type}'. "
                f"Valid: {sorted(VALID_MSG_TYPES)}"
            )
        msg = {
            "type": msg_type,
            "from": from_name,
            "content": content,
            "ts": time.time(),
        }
        msg.update(extra)
        line = json.dumps(msg)
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(
                self._dir / f"{to_name}.jsonl", "a", encoding="utf-8",
            ) as f:
                f.write(line + "\n")
        return f"Sent {msg_type} to {to_name}"

    def _parse_inbox(self, path: Path) -> list[dict]:
        messages: list[dict] = []
        for line in path.read_text(encoding="utf-8").strip().splitlines():
            if line.strip():
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return messages

    def peek_inbox(self, name: str) -> list[dict]:
        path = self._dir / f"{name}.jsonl"
        with self._lock:
            if not path.is_file() or path.stat().st_size == 0:
                return []
            return self._parse_inbox(path)

    def read_inbox(self, name: str) -> list[dict]:
        path = self._dir / f"{name}.jsonl"
        with self._lock:
            if not path.is_file() or path.stat().st_size == 0:
                return []
            messages = self._parse_inbox(path)
            path.write_text("", encoding="utf-8")
        return messages


# ---------------------------------------------------------------------------
# ProtocolTracker
# ---------------------------------------------------------------------------


class ProtocolTracker:
    """Track request-response protocol state (shutdown, plan approval).

    Shared FSM: [pending] --approve--> [approved]
                [pending] --reject---> [rejected]
    """

    def __init__(self) -> None:
        self._shutdown: dict[str, dict] = {}
        self._plans: dict[str, dict] = {}
        self._lock = threading.Lock()

    def open_shutdown(self, target: str) -> str:
        req_id = uuid.uuid4().hex[:8]
        with self._lock:
            self._shutdown[req_id] = {"target": target, "status": "pending"}
        return req_id

    def open_plan(self, from_name: str, plan: str) -> str:
        req_id = uuid.uuid4().hex[:8]
        with self._lock:
            self._plans[req_id] = {
                "from": from_name, "plan": plan, "status": "pending",
            }
        return req_id

    def resolve(self, req_id: str, approve: bool) -> str:
        new_status = "approved" if approve else "rejected"
        with self._lock:
            for store in (self._shutdown, self._plans):
                if req_id in store:
                    if store[req_id]["status"] != "pending":
                        return (
                            f"[error] Request {req_id} already "
                            f"{store[req_id]['status']}"
                        )
                    store[req_id]["status"] = new_status
                    return new_status
        return f"[error] Unknown request_id '{req_id}'"

    def get(self, req_id: str) -> dict | None:
        with self._lock:
            for store in (self._shutdown, self._plans):
                if req_id in store:
                    return dict(store[req_id])
        return None


# ---------------------------------------------------------------------------
# Teammate
# ---------------------------------------------------------------------------


def _format_team_message(msg: dict) -> str:
    from_name = msg.get("from", "unknown")
    msg_type = msg.get("type", "message")
    content = msg.get("content", "")
    attrs = f'from="{from_name}" msg_type="{msg_type}"'
    if "req_id" in msg:
        attrs += f' req_id="{msg["req_id"]}"'
    if "approve" in msg:
        attrs += f' approve="{msg["approve"]}"'
    return f"<team-message {attrs}>\n{content}\n</team-message>"


class Teammate:
    """Single-conversation agent on a daemon thread.

    Thread waits for inbox messages, runs one LLM conversation, then exits.
    """

    LEAD = "lead"

    def __init__(
        self,
        spec: TeammateSpec,
        client: anthropic.Anthropic,
        bus: Bus,
        profiler: Profiler,
        *,
        protocol: ProtocolTracker | None = None,
        skill_registry: SkillRegistry | None = None,
        active_names_fn: callable = lambda: [],
        done_fn: callable | None = None,
    ) -> None:
        self.spec = spec
        self._client = client
        self._bus = bus
        self._profiler = profiler
        self._protocol = protocol
        self._skill_registry = skill_registry
        self._active_names_fn = active_names_fn
        self._done_fn = done_fn
        self._status = "working"
        self._shutdown = threading.Event()
        self._approved_shutdown = False

    @property
    def status(self) -> str:
        return self._status

    def start(self) -> None:
        self._shutdown.clear()
        self._status = "working"
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def stop(self) -> None:
        self._shutdown.set()

    # -- Single-shot entry point --------------------------------------------

    def _run(self) -> None:
        """Wait for inbox messages, process one conversation, then exit."""
        while not self._shutdown.is_set():
            inbox = self._bus.read_inbox(self.spec.name)
            if inbox:
                break
            self._shutdown.wait(timeout=2)
        else:
            self._finish()
            return

        self._process(inbox)
        self._finish()

    def _finish(self) -> None:
        self._status = "done"
        if self._done_fn:
            self._done_fn(self.spec.name)

    # -- LLM loop -----------------------------------------------------------

    def _process(self, inbox_messages: list[dict]) -> None:
        """Run one LLM conversation for a batch of inbox messages."""
        initial_text = "\n".join(
            _format_team_message(m) for m in inbox_messages
        )
        messages: list[dict] = [
            {"role": "user", "content": [{"type": "text", "text": initial_text}]},
        ]
        tools = self._build_tools()
        system = self._system_prompt()

        for _ in range(self.spec.max_turns):
            self._inject_inbox(messages)
            if self._approved_shutdown:
                break

            self._profiler.start(f"api:teammate:{self.spec.name}")
            try:
                response = self._client.messages.create(
                    model=self.spec.model,
                    max_tokens=settings.max_tokens,
                    system=system,
                    tools=tools,
                    messages=messages,
                )
            except Exception:
                break
            usage = response.usage
            self._profiler.stop(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                stop=response.stop_reason,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                break

            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    if settings.verbose and hasattr(block, "text"):
                        print(f"  [{self.spec.name}] {block.text}")
                    continue
                if settings.verbose:
                    print(f"  [{self.spec.name}] > {block.name}")
                result = self._exec_tool(block.name, block.input)
                if len(result) > MAX_TOOL_OUTPUT:
                    result = result[:MAX_TOOL_OUTPUT] + "\n\n[truncated]"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
            messages.append({"role": "user", "content": tool_results})

    def _inject_inbox(self, messages: list[dict]) -> None:
        """Drain inbox and inject new messages into the last user turn."""
        new_inbox = self._bus.read_inbox(self.spec.name)
        if not new_inbox:
            return
        parts = [_format_team_message(msg) for msg in new_inbox]
        last = messages[-1]
        if last["role"] == "user" and isinstance(last["content"], list):
            for text in parts:
                last["content"].append({"type": "text", "text": text})

    # -- Tool dispatch ------------------------------------------------------

    def _exec_tool(self, name: str, tool_input: dict) -> str:
        if name == "send":
            return self._handle_send(tool_input)
        if name == "read_inbox":
            msgs = self._bus.read_inbox(self.spec.name)
            if not msgs:
                return "(no messages)"
            return json.dumps(msgs, indent=2)
        if name == "load_skill" and self._skill_registry is not None:
            self._profiler.start("tool:load_skill")
            result = self._skill_registry.load(tool_input["name"])
            self._profiler.stop()
            return result
        self._profiler.start(f"tool:{name}")
        result = execute_tool(name, tool_input)
        self._profiler.stop()
        return result

    def _handle_send(self, tool_input: dict) -> str:
        to = tool_input["to"]
        content = tool_input["content"]
        msg_type = tool_input.get("msg_type", "message")
        extra: dict = {}
        for key in ("req_id", "approve"):
            if key in tool_input:
                extra[key] = tool_input[key]
        if msg_type == "broadcast":
            count = 0
            for name in self._active_names_fn():
                if name != self.spec.name:
                    self._bus.send(self.spec.name, name, content, "broadcast")
                    count += 1
            self._bus.send(self.spec.name, self.LEAD, content, "broadcast")
            return f"Broadcast to {count} teammates + lead"
        if self._protocol is not None:
            if msg_type == "plan_request":
                extra["req_id"] = self._protocol.open_plan(
                    self.spec.name, content,
                )
            elif msg_type == "shutdown_response":
                req_id = extra.get("req_id")
                approve = extra.get("approve", False)
                if req_id:
                    self._protocol.resolve(req_id, approve)
                if approve:
                    self._approved_shutdown = True
        return self._bus.send(self.spec.name, to, content, msg_type, **extra)

    # -- Tool & prompt construction -----------------------------------------

    def _build_tools(self) -> list[dict]:
        from pip_agent.tools import ALL_TOOLS

        allowed = set(self.spec.tools)
        tools = [t for t in ALL_TOOLS if t["name"] in allowed]
        tools.append(SEND_SCHEMA)
        tools.append(READ_INBOX_SCHEMA)
        if self._skill_registry is not None and self._skill_registry.available:
            tools.append(self._skill_registry.tool_schema())
        return tools

    def _system_prompt(self) -> str:
        base = (
            f"You are '{self.spec.name}', a teammate in a collaborative agent team.\n"
            f"Working directory: {WORKDIR}\n"
            f"Use the 'send' tool to communicate with teammates or 'lead'.\n"
        )
        if self.spec.system_body:
            return base + "\n" + self.spec.system_body
        return base


# ---------------------------------------------------------------------------
# TeamManager
# ---------------------------------------------------------------------------


class TeamManager:
    """Orchestrate teammate discovery, spawning, and communication."""

    LEAD = "lead"

    def __init__(
        self,
        builtin_dir: Path,
        user_dir: Path,
        client: anthropic.Anthropic,
        profiler: Profiler,
        *,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self._client = client
        self._profiler = profiler
        self._skill_registry = skill_registry
        self._roster: dict[str, TeammateSpec] = {}
        self._active: dict[str, Teammate] = {}
        self._bus = Bus(user_dir / "inbox")
        self._protocol = ProtocolTracker()
        self._builtin_dir = builtin_dir
        self._user_dir = user_dir

        self._scan_dir(builtin_dir)
        self._scan_dir(user_dir)

    def _scan_dir(self, directory: Path) -> None:
        if not directory.is_dir():
            return
        for md_path in sorted(directory.glob("*.md")):
            try:
                spec = TeammateSpec.from_file(md_path)
                self._roster[spec.name] = spec
            except Exception:
                continue

    def _active_names(self) -> list[str]:
        return list(self._active.keys())

    def _on_done(self, name: str) -> None:
        self._active.pop(name, None)

    def _make_teammate(self, spec: TeammateSpec) -> Teammate:
        return Teammate(
            spec,
            self._client,
            self._bus,
            self._profiler,
            protocol=self._protocol,
            skill_registry=self._skill_registry,
            active_names_fn=self._active_names,
            done_fn=self._on_done,
        )

    # -- Public API (called from agent_loop) --------------------------------

    def _rescan(self) -> None:
        self._scan_dir(self._builtin_dir)
        self._scan_dir(self._user_dir)

    def spawn(self, name: str, prompt: str) -> str:
        if name in self._active:
            return f"[error] '{name}' is already working."
        spec = self._roster.get(name)
        if spec is None:
            self._rescan()
            spec = self._roster.get(name)
        if spec is None:
            available = ", ".join(sorted(self._roster.keys())) or "(none)"
            return f"[error] Unknown teammate '{name}'. Available: {available}"
        teammate = self._make_teammate(spec)
        teammate.start()
        self._active[name] = teammate
        self._bus.send(self.LEAD, name, prompt, "message")
        return f"Spawned '{name}' ({spec.model}, max {spec.max_turns} turns)."

    def send(
        self, to: str, content: str, msg_type: str = "message", **extra,
    ) -> str:
        if msg_type == "broadcast":
            count = 0
            for name in list(self._active):
                self._bus.send(self.LEAD, name, content, "broadcast")
                count += 1
            return f"Broadcast to {count} teammates."
        if msg_type == "shutdown_request":
            extra["req_id"] = self._protocol.open_shutdown(to)
        elif msg_type == "plan_response":
            req_id = extra.get("req_id")
            approve = extra.get("approve", False)
            if req_id:
                self._protocol.resolve(req_id, approve)
        result = self._bus.send(self.LEAD, to, content, msg_type, **extra)
        if to not in self._active:
            return f"{result} (offline — will be read on next activation)"
        return result

    def peek_inbox(self) -> list[dict]:
        return self._bus.peek_inbox(self.LEAD)

    def read_inbox(self) -> list[dict]:
        return self._bus.read_inbox(self.LEAD)

    def status(self) -> str:
        self._rescan()
        if not self._roster:
            return "No teammates defined."
        lines: list[str] = []
        for name in sorted(self._roster):
            spec = self._roster[name]
            state = "working" if name in self._active else "available"
            lines.append(
                f"  {name} [{state}] {spec.description} ({spec.model})"
            )
        return "\n".join(lines)

    def deactivate_all(self) -> None:
        for t in self._active.values():
            t.stop()
        self._active.clear()
