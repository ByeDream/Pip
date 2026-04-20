"""Unit tests for the pure-logic parts of ``host_scheduler`` + job CRUD.

The background thread is not exercised here (that lands in Phase 11's
integration harness). These tests focus on determinism: schedule maths,
active-window calculation, cron.json persistence, and enqueue side effects.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from pip_agent.channels import InboundMessage
from pip_agent.host_scheduler import (
    HostScheduler,
    _in_active_window,
    _next_cron_fire,
    _next_fire_at,
    _Sender,
    _validate_schedule,
)

# ---------------------------------------------------------------------------
# Schedule maths
# ---------------------------------------------------------------------------


class TestNextFireAt:
    def test_at_future(self):
        now = 1_000_000.0
        assert _next_fire_at("at", {"timestamp": now + 500}, now=now) == now + 500

    def test_at_past_returns_none(self):
        now = 1_000_000.0
        assert _next_fire_at("at", {"timestamp": now - 500}, now=now) is None

    def test_every_adds_seconds(self):
        now = 1_000_000.0
        assert _next_fire_at("every", {"seconds": 60}, now=now) == now + 60

    def test_every_non_positive_is_none(self):
        assert _next_fire_at("every", {"seconds": 0}, now=0) is None
        assert _next_fire_at("every", {"seconds": -5}, now=0) is None

    def test_unknown_kind(self):
        assert _next_fire_at("weekly", {}, now=0) is None


class TestNextCronFire:
    def test_daily_future_same_day(self):
        now_dt = datetime(2026, 4, 20, 8, 0, 0)
        now = now_dt.timestamp()
        fire = _next_cron_fire("30 9 * * *", now=now)
        assert fire is not None
        assert datetime.fromtimestamp(fire) == datetime(2026, 4, 20, 9, 30, 0)

    def test_daily_past_rolls_to_tomorrow(self):
        now = datetime(2026, 4, 20, 10, 0, 0).timestamp()
        fire = _next_cron_fire("30 9 * * *", now=now)
        assert fire is not None
        assert datetime.fromtimestamp(fire) == datetime(2026, 4, 21, 9, 30, 0)

    def test_hourly_advances_one_hour_when_past(self):
        now = datetime(2026, 4, 20, 10, 31, 0).timestamp()
        fire = _next_cron_fire("30 * * * *", now=now)
        assert fire is not None
        assert datetime.fromtimestamp(fire) == datetime(2026, 4, 20, 11, 30, 0)

    def test_hourly_in_current_hour_when_future(self):
        now = datetime(2026, 4, 20, 10, 15, 0).timestamp()
        fire = _next_cron_fire("30 * * * *", now=now)
        assert datetime.fromtimestamp(fire) == datetime(2026, 4, 20, 10, 30, 0)

    def test_rejects_dom(self):
        assert _next_cron_fire("0 9 1 * *", now=time.time()) is None

    def test_rejects_bad_grammar(self):
        assert _next_cron_fire("garbage", now=time.time()) is None
        assert _next_cron_fire("99 25 * * *", now=time.time()) is None


class TestValidateSchedule:
    def test_at_requires_timestamp(self):
        assert _validate_schedule("at", {}) is not None

    def test_every_requires_positive_seconds(self):
        assert _validate_schedule("every", {"seconds": 0}) is not None
        assert _validate_schedule("every", {"seconds": 60}) is None

    def test_cron_rejects_unsupported(self):
        assert _validate_schedule("cron", {"expr": "*/5 * * * *"}) is not None

    def test_cron_accepts_daily(self):
        assert _validate_schedule("cron", {"expr": "0 9 * * *"}) is None


# ---------------------------------------------------------------------------
# Active window
# ---------------------------------------------------------------------------


class TestActiveWindow:
    @patch("pip_agent.host_scheduler.settings")
    def test_daytime_window(self, mock_settings):
        mock_settings.heartbeat_active_start = 9
        mock_settings.heartbeat_active_end = 22
        noon = datetime(2026, 4, 20, 12, 0, 0).timestamp()
        early = datetime(2026, 4, 20, 6, 0, 0).timestamp()
        assert _in_active_window(noon) is True
        assert _in_active_window(early) is False

    @patch("pip_agent.host_scheduler.settings")
    def test_wrap_around_night(self, mock_settings):
        mock_settings.heartbeat_active_start = 22
        mock_settings.heartbeat_active_end = 6
        midnight = datetime(2026, 4, 20, 1, 0, 0).timestamp()
        afternoon = datetime(2026, 4, 20, 13, 0, 0).timestamp()
        assert _in_active_window(midnight) is True
        assert _in_active_window(afternoon) is False


# ---------------------------------------------------------------------------
# Scheduler job CRUD + enqueue
# ---------------------------------------------------------------------------


def _make_sched(tmp_path: Path) -> tuple[HostScheduler, list, threading.Lock]:
    queue: list = []
    lock = threading.Lock()
    stop = threading.Event()
    sched = HostScheduler(
        agents_dir=tmp_path / "agents",
        msg_queue=queue,
        q_lock=lock,
        stop_event=stop,
    )
    (tmp_path / "agents" / "pip-boy").mkdir(parents=True)
    return sched, queue, lock


class TestSchedulerJobCrud:
    def test_add_every_persists_to_disk(self, tmp_path: Path):
        sched, _, _ = _make_sched(tmp_path)
        reply = sched.add_job(
            name="tick", schedule_kind="every", schedule_config={"seconds": 60},
            message="hello", channel="cli", peer_id="cli-user",
            sender_id="owner", agent_id="pip-boy",
        )
        assert "Scheduled 'tick'" in reply
        assert (tmp_path / "agents" / "pip-boy" / "cron.json").is_file()
        jobs = sched.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["message"] == "hello"

    def test_add_rejects_invalid_schedule(self, tmp_path: Path):
        sched, _, _ = _make_sched(tmp_path)
        reply = sched.add_job(
            name="bad", schedule_kind="every", schedule_config={"seconds": 0},
            message="x", channel="cli", peer_id="p",
            sender_id="s", agent_id="pip-boy",
        )
        assert reply.startswith("Error:")
        assert sched.list_jobs() == []

    def test_add_requires_agent_id(self, tmp_path: Path):
        sched, _, _ = _make_sched(tmp_path)
        reply = sched.add_job(
            name="n", schedule_kind="every", schedule_config={"seconds": 60},
            message="m", channel="cli", peer_id="p",
            sender_id="s", agent_id="",
        )
        assert "agent_id" in reply

    def test_remove_by_id(self, tmp_path: Path):
        sched, _, _ = _make_sched(tmp_path)
        sched.add_job(
            name="a", schedule_kind="every", schedule_config={"seconds": 60},
            message="m", channel="cli", peer_id="p",
            sender_id="s", agent_id="pip-boy",
        )
        jid = sched.list_jobs()[0]["id"]
        assert "Removed" in sched.remove_job(jid)
        assert sched.list_jobs() == []

    def test_update_changes_fields(self, tmp_path: Path):
        sched, _, _ = _make_sched(tmp_path)
        sched.add_job(
            name="a", schedule_kind="every", schedule_config={"seconds": 60},
            message="old", channel="cli", peer_id="p",
            sender_id="s", agent_id="pip-boy",
        )
        jid = sched.list_jobs()[0]["id"]
        assert "Updated" in sched.update_job(jid, message="new", enabled=False)
        job = sched.list_jobs()[0]
        assert job["message"] == "new"
        assert job["enabled"] is False

    def test_update_unknown_id(self, tmp_path: Path):
        sched, _, _ = _make_sched(tmp_path)
        assert "not found" in sched.update_job("nosuchid", message="x")


class TestSchedulerTickFiresJobs:
    def test_every_job_fires_and_reschedules(self, tmp_path: Path):
        sched, queue, _ = _make_sched(tmp_path)
        sched.add_job(
            name="beep", schedule_kind="every", schedule_config={"seconds": 60},
            message="ping", channel="cli", peer_id="cli-user",
            sender_id="owner", agent_id="pip-boy",
        )
        # Force the single job's next_fire_at into the past and tick.
        jobs = sched._load_jobs("pip-boy")
        jobs[0]["next_fire_at"] = time.time() - 1
        sched._save_jobs("pip-boy", jobs)

        sched._tick(time.time())

        assert len(queue) == 1
        msg = queue[0]
        assert isinstance(msg, InboundMessage)
        assert msg.sender_id == _Sender.CRON
        assert msg.text == "ping"
        assert msg.agent_id == "pip-boy"
        # Next fire was rescheduled into the future.
        assert sched._load_jobs("pip-boy")[0]["next_fire_at"] > time.time()

    def test_at_job_disables_after_firing(self, tmp_path: Path):
        sched, queue, _ = _make_sched(tmp_path)
        past = time.time() - 10
        sched.add_job(
            name="once", schedule_kind="at",
            schedule_config={"timestamp": time.time() + 100},  # first must pass validation
            message="go", channel="cli", peer_id="cli-user",
            sender_id="owner", agent_id="pip-boy",
        )
        # Then force fire_at into the past for this tick.
        jobs = sched._load_jobs("pip-boy")
        jobs[0]["next_fire_at"] = past
        sched._save_jobs("pip-boy", jobs)

        sched._tick(time.time())
        assert len(queue) == 1
        assert sched._load_jobs("pip-boy")[0]["enabled"] is False


class TestHeartbeatTick:
    @patch("pip_agent.host_scheduler.settings")
    def test_heartbeat_fires_when_md_present(self, mock_settings, tmp_path: Path):
        mock_settings.heartbeat_interval = 60
        mock_settings.heartbeat_active_start = 0
        mock_settings.heartbeat_active_end = 24
        sched, queue, _ = _make_sched(tmp_path)
        (tmp_path / "agents" / "pip-boy" / "HEARTBEAT.md").write_text(
            "check memory", encoding="utf-8",
        )
        sched._tick(time.time())
        assert len(queue) == 1
        assert queue[0].sender_id == _Sender.HEARTBEAT
        assert queue[0].text == "check memory"

    @patch("pip_agent.host_scheduler.settings")
    def test_heartbeat_respects_interval(self, mock_settings, tmp_path: Path):
        mock_settings.heartbeat_interval = 60
        mock_settings.heartbeat_active_start = 0
        mock_settings.heartbeat_active_end = 24
        sched, queue, _ = _make_sched(tmp_path)
        (tmp_path / "agents" / "pip-boy" / "HEARTBEAT.md").write_text(
            "ping", encoding="utf-8",
        )
        sched._tick(time.time())
        sched._tick(time.time())  # same tick, no double-fire
        assert len(queue) == 1

    @patch("pip_agent.host_scheduler.settings")
    def test_heartbeat_skipped_outside_window(self, mock_settings, tmp_path: Path):
        mock_settings.heartbeat_interval = 60
        mock_settings.heartbeat_active_start = 9
        mock_settings.heartbeat_active_end = 22
        sched, queue, _ = _make_sched(tmp_path)
        (tmp_path / "agents" / "pip-boy" / "HEARTBEAT.md").write_text(
            "ping", encoding="utf-8",
        )
        early = datetime(2026, 4, 20, 3, 0, 0).timestamp()
        sched._tick(early)
        assert queue == []
