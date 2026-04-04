from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TodoStatus = Literal["pending", "in_progress", "completed"]

MAX_ITEMS = 20


@dataclass
class TodoItem:
    id: str
    content: str
    status: TodoStatus = "pending"


@dataclass
class TodoManager:
    """Session-scoped task list the LLM maintains via the todo_write tool."""

    items: list[TodoItem] = field(default_factory=list)

    def write(self, todos: list[dict]) -> str:
        """Upsert items by id with validation."""
        if len(todos) > MAX_ITEMS:
            raise ValueError(f"Max {MAX_ITEMS} todos allowed")

        index = {item.id: item for item in self.items}
        in_progress_count = 0

        for entry in todos:
            tid = str(entry.get("id", ""))
            content = str(entry.get("content", "")).strip()
            status = str(entry.get("status", "pending")).lower()

            if not content:
                raise ValueError(f"Item {tid}: content required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {tid}: invalid status '{status}'")
            if status == "in_progress":
                in_progress_count += 1

            if tid in index:
                index[tid].content = content
                index[tid].status = status  # type: ignore[assignment]
            else:
                index[tid] = TodoItem(id=tid, content=content, status=status)  # type: ignore[arg-type]

        if in_progress_count > 1:
            raise ValueError("Only one task can be in_progress at a time")

        self.items = list(index.values())
        return self.render()

    def has_items(self) -> bool:
        return bool(self.items)

    def render(self) -> str:
        if not self.items:
            return "(no todos)"
        status_icon = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
        lines: list[str] = []
        for item in self.items:
            icon = status_icon.get(item.status, "[ ]")
            lines.append(f"  {icon} {item.content}  (id: {item.id})")
        done = sum(1 for t in self.items if t.status == "completed")
        lines.append(f"\n  ({done}/{len(self.items)} completed)")
        return "\n".join(lines)
