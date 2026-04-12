"""Memory subsystem: per-agent behavioral memory with three-tier pipeline.

Storage layout:
    .pip/memory/<agent-id>/
        state.json
        observations/<date>.jsonl
        memories.json
        axioms.md
    .pip/user.md              (global, cross-agent)
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class MemoryStore:
    """Facade for a single agent's memory storage.

    All file I/O is lazy — missing files are silently handled with defaults.
    """

    def __init__(self, base_dir: Path, agent_id: str) -> None:
        self.agent_id = agent_id
        self.agent_dir = base_dir / agent_id
        self.pip_dir = base_dir.parent  # .pip/
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        (self.agent_dir / "observations").mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def load_state(self) -> dict[str, Any]:
        path = self.agent_dir / "state.json"
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save_state(self, state: dict[str, Any]) -> None:
        path = self.agent_dir / "state.json"
        path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Observations (L1)
    # ------------------------------------------------------------------

    def write_observations(self, observations: list[dict[str, Any]]) -> None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.agent_dir / "observations" / f"{date_str}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for obs in observations:
                f.write(json.dumps(obs, ensure_ascii=False) + "\n")

    def write_single(
        self, text: str, category: str = "observation", source: str = "user",
    ) -> None:
        """Write a single observation (used by memory_write tool)."""
        obs = {
            "ts": time.time(),
            "text": text,
            "category": category,
            "source": source,
        }
        self.write_observations([obs])

    def load_all_observations(self) -> list[dict[str, Any]]:
        obs_dir = self.agent_dir / "observations"
        if not obs_dir.is_dir():
            return []
        result: list[dict[str, Any]] = []
        for fp in sorted(obs_dir.glob("*.jsonl")):
            try:
                for line in fp.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        result.append(json.loads(line))
            except (json.JSONDecodeError, OSError):
                continue
        return result

    # ------------------------------------------------------------------
    # Memories (L2)
    # ------------------------------------------------------------------

    def load_memories(self) -> list[dict[str, Any]]:
        path = self.agent_dir / "memories.json"
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def save_memories(self, memories: list[dict[str, Any]]) -> None:
        path = self.agent_dir / "memories.json"
        path.write_text(
            json.dumps(memories, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Axioms (L3)
    # ------------------------------------------------------------------

    def load_axioms(self) -> str:
        path = self.agent_dir / "axioms.md"
        if not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def save_axioms(self, text: str) -> None:
        path = self.agent_dir / "axioms.md"
        path.write_text(text, encoding="utf-8")

    # ------------------------------------------------------------------
    # User profile (global)
    # ------------------------------------------------------------------

    def load_user_profile(self) -> str:
        path = self.pip_dir / "user.md"
        if not path.is_file():
            return ""
        try:
            text = path.read_text(encoding="utf-8").strip()
            if not any(
                line.strip().startswith("- **") and not line.strip().endswith("**")
                for line in text.splitlines()
                if ":" in line
            ):
                return ""
            return text
        except OSError:
            return ""

    def update_user_profile(
        self,
        *,
        sender_id: str = "",
        channel: str = "",
        **fields: str,
    ) -> str:
        """Update specific fields in user.md. Returns confirmation message.

        Supported fields: name, call_me, timezone, notes.
        sender_id + channel are auto-captured from the conversation context
        and stored as identifiers for cross-session user recognition.
        """
        path = self.pip_dir / "user.md"
        field_map = {
            "name": "Name",
            "call_me": "What to call them",
            "timezone": "Timezone",
            "notes": "Notes",
        }

        current: dict[str, str] = {}
        current_ids: list[str] = []
        if path.is_file():
            try:
                in_ids = False
                for line in path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    for key, label in field_map.items():
                        prefix = f"- **{label}:**"
                        if stripped.startswith(prefix):
                            current[key] = stripped[len(prefix):].strip()
                    if stripped == "- **Identifiers:**":
                        in_ids = True
                        continue
                    if in_ids:
                        if stripped.startswith("- `") and stripped.endswith("`"):
                            current_ids.append(stripped[3:-1])
                        elif stripped.startswith("- **"):
                            in_ids = False
            except OSError:
                pass

        updated_keys: list[str] = []
        for key, value in fields.items():
            if key not in field_map or not value:
                continue
            if key == "notes" and current.get("notes"):
                current[key] = current[key] + "; " + value
            else:
                current[key] = value
            updated_keys.append(key)

        if sender_id and channel:
            new_id = f"{channel}:{sender_id}"
            if new_id not in current_ids:
                current_ids.append(new_id)
                updated_keys.append("identifier")

        if not updated_keys:
            return "No fields to update."

        lines = ["# About Your Human", "", "_Learn about the person you're helping. Update this as you go._", ""]
        for key, label in field_map.items():
            val = current.get(key, "")
            lines.append(f"- **{label}:** {val}")
        if current_ids:
            lines.append("- **Identifiers:**")
            for ident in current_ids:
                lines.append(f"  - `{ident}`")
        lines.append("")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return f"Updated user profile: {', '.join(updated_keys)}"

    # ------------------------------------------------------------------
    # Search / Recall
    # ------------------------------------------------------------------

    def search(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        from pip_agent.memory.recall import search_memories
        memories = self.load_memories()
        if not memories:
            observations = self.load_all_observations()
            if not observations:
                return []
            search_pool = [
                {"text": o.get("text", ""), "last_reinforced": o.get("ts", 0)}
                for o in observations
            ]
            return search_memories(query, search_pool, top_k=top_k)
        return search_memories(query, memories, top_k=top_k)

    def auto_recall(self, user_text: str, *, top_k: int = 3) -> str:
        """Return formatted string of recalled memories for prompt injection."""
        if not user_text.strip():
            return ""
        results = self.search(user_text, top_k=top_k)
        if not results:
            return ""
        lines: list[str] = []
        for r in results:
            lines.append(f"- {r.get('text', '')} (score: {r.get('score', 0)})")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Prompt enrichment
    # ------------------------------------------------------------------

    def enrich_prompt(
        self,
        system_prompt: str,
        user_text: str,
        *,
        channel: str = "cli",
        agent_id: str = "",
        workdir: str = "",
        sender_id: str = "",
    ) -> str:
        """Inject dynamic context into the system prompt.

        Layers (injected in order):
          1. ## User — global user profile (only if sender matches or no identifiers yet)
          2. ## Judgment Principles — per-agent axioms
          3. ## Recalled Context — TF-IDF matched memories
          4. ## Context — runtime metadata
          5. ## Channel — channel hints
        """
        user_profile = self.load_user_profile()
        if user_profile and self._sender_matches(user_profile, channel, sender_id):
            system_prompt = _insert_after_identity(
                system_prompt, f"## User\n\n{user_profile}",
            )

        axioms = self.load_axioms()
        if axioms:
            system_prompt = _insert_before_rules(
                system_prompt, f"## Judgment Principles\n\n{axioms}",
            )

        recalled = self.auto_recall(user_text)
        if recalled:
            system_prompt += f"\n\n## Recalled Context\n\n{recalled}"

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        system_prompt += (
            f"\n\n## Context\n\n"
            f"Agent: {agent_id}\nWorking directory: {workdir}\nTime: {now}"
        )

        hints = {
            "cli": "You are responding via a terminal. Markdown is supported.",
            "wechat": "You are responding via WeChat. Keep messages concise. No markdown.",
            "wecom": "You are responding via WeCom. Keep messages under 2000 chars.",
        }
        if channel in hints:
            system_prompt += f"\n\n## Channel\n\n{hints[channel]}"

        return system_prompt

    @staticmethod
    def _sender_matches(profile_text: str, channel: str, sender_id: str) -> bool:
        """Check if the current sender matches a known identifier.

        Returns True (inject profile) when:
        - No identifiers stored yet (cold start — assume it's the owner)
        - Current channel:sender_id matches a stored identifier
        Returns False when identifiers exist but none match.
        """
        stored_ids: list[str] = []
        for line in profile_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- `") and stripped.endswith("`"):
                stored_ids.append(stripped[3:-1])

        if not stored_ids:
            return True

        if not sender_id:
            return True

        current = f"{channel}:{sender_id}"
        return current in stored_ids

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        memories = self.load_memories()
        observations = self.load_all_observations()
        axioms = self.load_axioms()
        state = self.load_state()
        return {
            "agent_id": self.agent_id,
            "memories": len(memories),
            "observations": len(observations),
            "has_axioms": bool(axioms),
            "axiom_lines": len(axioms.splitlines()) if axioms else 0,
            "last_reflect_at": state.get("last_reflect_at"),
            "last_consolidate_at": state.get("last_consolidate_at"),
        }


# ----------------------------------------------------------------------
# Prompt section helpers
# ----------------------------------------------------------------------

_IDENTITY_RE = re.compile(r"^## Identity\b", re.MULTILINE)
_RULES_RE = re.compile(r"^## Rules\b", re.MULTILINE)


def _insert_after_identity(prompt: str, section: str) -> str:
    """Insert a section after ## Identity (before next ##). Falls back to prepend."""
    m = _IDENTITY_RE.search(prompt)
    if not m:
        return section + "\n\n" + prompt

    next_heading = re.search(r"^## ", prompt[m.end():], re.MULTILINE)
    if next_heading:
        pos = m.end() + next_heading.start()
    else:
        pos = len(prompt)

    return prompt[:pos].rstrip() + "\n\n" + section + "\n\n" + prompt[pos:].lstrip()


def _insert_before_rules(prompt: str, section: str) -> str:
    """Insert a section just before ## Rules. Falls back to append."""
    m = _RULES_RE.search(prompt)
    if not m:
        return prompt + "\n\n" + section
    return prompt[:m.start()].rstrip() + "\n\n" + section + "\n\n" + prompt[m.start():]
