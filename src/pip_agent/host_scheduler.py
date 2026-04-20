"""Host-level scheduler: cron jobs + heartbeat pings.

The scheduler is intentionally lean. It does not run tool calls, touch memory,
or talk to the SDK directly. Its only job is to push ``InboundMessage``
instances into the host's inbound queue at the right time. The agent then
processes them through the same code path as any user-sent message.

Design:

* **One background thread per ``AgentHost``.** Ticks every ``_TICK_SECONDS``.
* **Per-agent cron store** at ``.pip/agents/<agent_id>/cron.json`` — a list of
  job dicts. Stored on disk so jobs survive restarts.
* **Per-agent heartbeat source** at ``.pip/agents/<agent_id>/HEARTBEAT.md``. If
  the file exists, the agent receives a ``<heartbeat>`` inbound every
  ``settings.heartbeat_interval`` seconds during the active window.
* **Sentinel sender ids** (see :class:`_Sender`) mark host-injected messages.
  Phase 4.6 uses these to wrap the prompt with ``<cron_task>`` / ``<heartbeat>``
  tags instead of ``<user_query>``.

Schedule kinds supported:

* ``at``       — one-shot, fires at an absolute epoch timestamp, then disables.
* ``every``    — repeating, fires every ``seconds`` interval.
* ``cron``     — minimal cron expression parser supporting ``"M H * * *"``
  (daily) and ``"M * * * *"`` (hourly). Anything more exotic returns an error
  at ``add_job`` time.

See ``docs/sdk-contract-notes.md`` for the full host ⇄ agent contract.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pip_agent.channels import InboundMessage
from pip_agent.config import settings
from pip_agent.fileutil import atomic_write

log = logging.getLogger(__name__)

_TICK_SECONDS = 5.0
_CRON_FILE = "cron.json"
_HEARTBEAT_FILE = "HEARTBEAT.md"


class _Sender:
    CRON = "__cron__"
    HEARTBEAT = "__heartbeat__"


@dataclass
class _HeartbeatState:
    last_fire_at: float = 0.0


# ---------------------------------------------------------------------------
# Schedule maths
# ---------------------------------------------------------------------------


def _next_fire_at(kind: str, cfg: dict[str, Any], *, now: float) -> float | None:
    """Return the next fire epoch, or ``None`` if the schedule is invalid/exhausted.

    Callers are expected to validate ``kind`` and ``cfg`` beforehand; this
    function is defensive but doesn't surface diagnostic strings.
    """
    if kind == "at":
        ts = float(cfg.get("timestamp", 0))
        if ts <= 0:
            return None
        return ts if ts > now else None
    if kind == "every":
        secs = int(cfg.get("seconds", 0))
        if secs <= 0:
            return None
        return now + secs
    if kind == "cron":
        return _next_cron_fire(cfg.get("expr", ""), now=now)
    return None


def _next_cron_fire(expr: str, *, now: float) -> float | None:
    """Compute the next fire time for a minimal cron expression.

    Supported forms:

    * ``"M H * * *"`` — fire daily at ``H:M`` local time.
    * ``"M * * * *"`` — fire hourly at minute ``M``.

    Returns ``None`` for anything outside this grammar. Phase 11 (or a later
    revision) can swap this out for ``croniter`` if we need day-of-week etc.
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        return None
    minute_s, hour_s, dom, mon, dow = parts
    if dom != "*" or mon != "*" or dow != "*":
        return None
    try:
        minute = int(minute_s)
    except ValueError:
        return None
    if not 0 <= minute <= 59:
        return None

    local_now = datetime.fromtimestamp(now)

    if hour_s == "*":
        target = local_now.replace(minute=minute, second=0, microsecond=0)
        ts = target.timestamp()
        while ts <= now:
            ts += 3600
        return ts

    try:
        hour = int(hour_s)
    except ValueError:
        return None
    if not 0 <= hour <= 23:
        return None

    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    ts = target.timestamp()
    while ts <= now:
        ts += 86400
    return ts


def _validate_schedule(kind: str, cfg: dict[str, Any]) -> str | None:
    """Return an error message if ``(kind, cfg)`` is unsupported, else ``None``."""
    if kind == "at":
        ts = cfg.get("timestamp")
        if not isinstance(ts, (int, float)) or float(ts) <= 0:
            return "schedule_kind='at' requires schedule_config.timestamp (epoch)."
        return None
    if kind == "every":
        secs = cfg.get("seconds")
        if not isinstance(secs, int) or secs <= 0:
            return "schedule_kind='every' requires schedule_config.seconds > 0."
        return None
    if kind == "cron":
        expr = cfg.get("expr")
        if not isinstance(expr, str) or not expr.strip():
            return "schedule_kind='cron' requires schedule_config.expr."
        # Probe the parser with a fixed ``now`` so we surface the error here
        # rather than silently dropping jobs during the tick loop.
        if _next_cron_fire(expr, now=time.time()) is None:
            return (
                "Unsupported cron expression. Supported forms: "
                "'M H * * *' (daily) or 'M * * * *' (hourly)."
            )
        return None
    return f"Unknown schedule_kind: {kind!r}."


# ---------------------------------------------------------------------------
# Heartbeat active window
# ---------------------------------------------------------------------------


def _in_active_window(now: float) -> bool:
    """Return True if ``now`` (local epoch) falls inside the heartbeat active window.

    Respects wrap-around (e.g. ``start=22``, ``end=6`` means "late night").
    """
    start = int(settings.heartbeat_active_start) % 24
    end = int(settings.heartbeat_active_end) % 24
    hour = datetime.fromtimestamp(now).hour
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


# ---------------------------------------------------------------------------
# HostScheduler
# ---------------------------------------------------------------------------


class HostScheduler:
    """Background cron + heartbeat injector for :class:`AgentHost`.

    The scheduler does not know about channels or the SDK. It just appends
    :class:`InboundMessage` to the host's queue under the shared lock. All
    retry / ACL / prompt-wrap logic lives in the normal inbound pipeline.
    """

    def __init__(
        self,
        *,
        agents_dir: Path,
        msg_queue: list[InboundMessage],
        q_lock: threading.Lock,
        stop_event: threading.Event,
    ) -> None:
        self._agents_dir = agents_dir
        self._msg_queue = msg_queue
        self._q_lock = q_lock
        self._stop_event = stop_event
        self._thread: threading.Thread | None = None
        self._heartbeat_state: dict[str, _HeartbeatState] = {}
        # File I/O needs its own lock so add/remove/update from the MCP thread
        # doesn't collide with the ticker thread re-reading jobs.
        self._io_lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="host-scheduler", daemon=True,
        )
        self._thread.start()
        log.info("HostScheduler started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    # -- public MCP-facing API ----------------------------------------------

    def add_job(
        self,
        *,
        name: str,
        schedule_kind: str,
        schedule_config: dict[str, Any],
        message: str,
        channel: str,
        peer_id: str,
        sender_id: str,
        agent_id: str,
    ) -> str:
        if not name:
            return "Error: 'name' is required."
        if not message:
            return "Error: 'message' is required."
        if not agent_id:
            return "Error: 'agent_id' is required (no active agent in context)."
        err = _validate_schedule(schedule_kind, schedule_config)
        if err:
            return f"Error: {err}"

        now = time.time()
        fire_at = _next_fire_at(schedule_kind, schedule_config, now=now)
        if fire_at is None:
            return "Error: schedule resolves to no future fire time."

        job = {
            "id": uuid.uuid4().hex[:8],
            "name": name,
            "enabled": True,
            "schedule_kind": schedule_kind,
            "schedule_config": schedule_config,
            "message": message,
            "channel": channel or "cli",
            "peer_id": peer_id or "cli-user",
            "sender_id": sender_id,
            "agent_id": agent_id,
            "created_at": now,
            "next_fire_at": fire_at,
            "last_fire_at": 0,
        }
        with self._io_lock:
            jobs = self._load_jobs(agent_id)
            jobs.append(job)
            self._save_jobs(agent_id, jobs)
        return f"Scheduled '{name}' (id={job['id']}, fires at {self._fmt(fire_at)})."

    def remove_job(self, job_id: str) -> str:
        if not job_id:
            return "Error: 'job_id' is required."
        with self._io_lock:
            for agent_dir in self._iter_agent_dirs():
                jobs = self._load_jobs(agent_dir.name)
                new_jobs = [j for j in jobs if j.get("id") != job_id]
                if len(new_jobs) != len(jobs):
                    self._save_jobs(agent_dir.name, new_jobs)
                    return f"Removed job {job_id}."
        return f"Job {job_id} not found."

    def update_job(self, job_id: str, **updates: Any) -> str:
        if not job_id:
            return "Error: 'job_id' is required."

        fields = {
            k: v for k, v in updates.items()
            if k in {"enabled", "name", "schedule_kind", "schedule_config", "message"}
        }
        if not fields:
            return "Nothing to update."

        with self._io_lock:
            for agent_dir in self._iter_agent_dirs():
                jobs = self._load_jobs(agent_dir.name)
                for job in jobs:
                    if job.get("id") != job_id:
                        continue
                    new_kind = fields.get("schedule_kind", job["schedule_kind"])
                    new_cfg = fields.get("schedule_config", job["schedule_config"])
                    if "schedule_kind" in fields or "schedule_config" in fields:
                        err = _validate_schedule(new_kind, new_cfg)
                        if err:
                            return f"Error: {err}"
                        fire_at = _next_fire_at(new_kind, new_cfg, now=time.time())
                        if fire_at is None:
                            return "Error: schedule resolves to no future fire time."
                        job["next_fire_at"] = fire_at
                    job.update(fields)
                    self._save_jobs(agent_dir.name, jobs)
                    return f"Updated job {job_id}."
        return f"Job {job_id} not found."

    def list_jobs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with self._io_lock:
            for agent_dir in self._iter_agent_dirs():
                out.extend(self._load_jobs(agent_dir.name))
        return out

    # -- internals -----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick(time.time())
            except Exception:
                log.exception("HostScheduler tick crashed; continuing")
            # ``wait`` returns True when the event is set -> clean exit.
            if self._stop_event.wait(_TICK_SECONDS):
                break
        log.info("HostScheduler stopped")

    def _tick(self, now: float) -> None:
        for agent_dir in self._iter_agent_dirs():
            self._tick_cron(agent_dir, now)
            self._tick_heartbeat(agent_dir, now)

    def _tick_cron(self, agent_dir: Path, now: float) -> None:
        agent_id = agent_dir.name
        with self._io_lock:
            jobs = self._load_jobs(agent_id)
        if not jobs:
            return

        dirty = False
        for job in jobs:
            if not job.get("enabled"):
                continue
            fire_at = float(job.get("next_fire_at") or 0)
            if fire_at <= 0 or fire_at > now:
                continue
            self._enqueue(
                InboundMessage(
                    text=job.get("message", ""),
                    sender_id=_Sender.CRON,
                    channel=job.get("channel") or "cli",
                    peer_id=job.get("peer_id") or "cli-user",
                    agent_id=agent_id,
                )
            )
            job["last_fire_at"] = now
            kind = job.get("schedule_kind", "")
            if kind == "at":
                job["enabled"] = False
                job["next_fire_at"] = 0
            else:
                nxt = _next_fire_at(kind, job.get("schedule_config", {}), now=now)
                if nxt is None:
                    job["enabled"] = False
                    job["next_fire_at"] = 0
                else:
                    job["next_fire_at"] = nxt
            dirty = True

        if dirty:
            with self._io_lock:
                self._save_jobs(agent_id, jobs)

    def _tick_heartbeat(self, agent_dir: Path, now: float) -> None:
        hb_file = agent_dir / _HEARTBEAT_FILE
        if not hb_file.is_file():
            return
        interval = int(settings.heartbeat_interval)
        if interval <= 0:
            return
        if not _in_active_window(now):
            return

        state = self._heartbeat_state.setdefault(agent_dir.name, _HeartbeatState())
        if now - state.last_fire_at < interval:
            return

        try:
            payload = hb_file.read_text("utf-8").strip()
        except OSError as exc:
            log.warning("Cannot read %s: %s", hb_file, exc)
            return
        if not payload:
            return

        self._enqueue(
            InboundMessage(
                text=payload,
                sender_id=_Sender.HEARTBEAT,
                channel="cli",
                peer_id="cli-user",
                agent_id=agent_dir.name,
            )
        )
        state.last_fire_at = now

    def _enqueue(self, inbound: InboundMessage) -> None:
        with self._q_lock:
            self._msg_queue.append(inbound)
        log.info(
            "HostScheduler enqueued %s for agent=%s",
            inbound.sender_id, inbound.agent_id,
        )

    def _iter_agent_dirs(self) -> list[Path]:
        if not self._agents_dir.is_dir():
            return []
        return [p for p in self._agents_dir.iterdir() if p.is_dir()]

    def _cron_path(self, agent_id: str) -> Path:
        return self._agents_dir / agent_id / _CRON_FILE

    def _load_jobs(self, agent_id: str) -> list[dict[str, Any]]:
        path = self._cron_path(agent_id)
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Corrupt cron.json at %s: %s — ignoring", path, exc)
            return []
        if not isinstance(data, list):
            return []
        return [j for j in data if isinstance(j, dict) and j.get("id")]

    def _save_jobs(self, agent_id: str, jobs: list[dict[str, Any]]) -> None:
        path = self._cron_path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(jobs, indent=2, ensure_ascii=False))

    @staticmethod
    def _fmt(ts: float) -> str:
        if ts <= 0:
            return "never"
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
