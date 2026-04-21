"""Tests for ``AgentHost._dispatch_reply``.

Covers the heartbeat-silencing contract plus the regular CLI / remote reply
paths. ``_dispatch_reply`` is extracted as a staticmethod precisely so these
branches can be exercised without spinning up the full SDK runtime.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pip_agent.agent_host import (
    _CRON_SENDER,
    _HEARTBEAT_SENDER,
    AgentHost,
    _is_ephemeral_sender,
)
from pip_agent.agent_runner import QueryResult
from pip_agent.channels import InboundMessage


def _heartbeat(text: str = "do the check") -> InboundMessage:
    return InboundMessage(
        text=text,
        sender_id="__heartbeat__",
        channel="cli",
        peer_id="cli-user",
    )


def _cli_user(text: str = "hi") -> InboundMessage:
    return InboundMessage(
        text=text, sender_id="cli-user", channel="cli", peer_id="cli-user",
    )


def _wecom_user(text: str = "hi") -> InboundMessage:
    return InboundMessage(
        text=text, sender_id="u-123", channel="wecom", peer_id="u-123",
    )


def _cron(text: str = "daily check") -> InboundMessage:
    return InboundMessage(
        text=text, sender_id="__cron__", channel="cli", peer_id="cli-user",
    )


class TestHeartbeatSentinelSilencing:
    """`HEARTBEAT_OK` is the "nothing to report" sentinel (see
    ``scaffold/heartbeat.md``). Only that exact reply is silenced; anything
    substantive — proactive greetings, reminders, alerts — flows through the
    normal dispatch path so the user actually sees it.
    """

    @pytest.mark.parametrize("text", [
        "HEARTBEAT_OK",
        "heartbeat_ok",
        "  HEARTBEAT_OK  ",
        "HEARTBEAT_OK.",
        "`HEARTBEAT_OK`",
        '"HEARTBEAT_OK"',
        "HEARTBEAT OK",
        "Heartbeat-Ok",
    ])
    def test_sentinel_variants_are_swallowed(self, text, capsys, caplog):
        caplog.set_level("INFO")

        AgentHost._dispatch_reply(
            inbound=_heartbeat(),
            result=QueryResult(text=text),
            ch=None,
            reply_peer="cli-user",
            session_key="k",
        )
        assert capsys.readouterr().out == "", f"expected silent stdout for {text!r}"
        assert any("heartbeat sentinel" in r.message.lower() for r in caplog.records)

    def test_substantive_heartbeat_reply_goes_to_cli(self, capsys):
        # A heartbeat saying "hey, you have 3 uncommitted files" must NOT be
        # silenced — that is the whole value of heartbeats. ``process_inbound``
        # calls ``run_query`` with ``stream_text=False`` for heartbeats so the
        # sentinel can be post-filtered, which means dispatch is the sole
        # source of heartbeat output and must print the full text itself.
        AgentHost._dispatch_reply(
            inbound=_heartbeat(),
            result=QueryResult(text="You have 3 uncommitted files on main."),
            ch=None,
            reply_peer="cli-user",
            session_key="k",
        )
        out = capsys.readouterr().out
        assert "3 uncommitted files" in out

    def test_heartbeat_reply_with_ok_as_substring_is_not_swallowed(self, capsys):
        # Word "ok" appearing inside a real message must not match the sentinel.
        AgentHost._dispatch_reply(
            inbound=_heartbeat(),
            result=QueryResult(text="Everything ok, but HEARTBEAT_OK? no, say hi."),
            ch=None,
            reply_peer="cli-user",
            session_key="k",
        )
        out = capsys.readouterr().out
        assert "say hi" in out

    def test_heartbeat_sentinel_not_sent_to_remote_channel(self, monkeypatch):
        sent: list[tuple[str, str]] = []

        def _fake_send(ch, peer, text):
            sent.append((peer, text))
            return True

        from pip_agent import agent_host
        monkeypatch.setattr(agent_host, "send_with_retry", _fake_send)

        inbound = InboundMessage(
            text="do the check",
            sender_id="__heartbeat__",
            channel="wecom",
            peer_id="u-123",
        )
        AgentHost._dispatch_reply(
            inbound=inbound,
            result=QueryResult(text="HEARTBEAT_OK"),
            ch=MagicMock(),
            reply_peer="u-123",
            session_key="k",
        )
        assert sent == []

    def test_substantive_heartbeat_reply_goes_to_remote_channel(self, monkeypatch):
        sent: list[tuple[str, str]] = []

        def _fake_send(ch, peer, text):
            sent.append((peer, text))
            return True

        from pip_agent import agent_host
        monkeypatch.setattr(agent_host, "send_with_retry", _fake_send)

        inbound = InboundMessage(
            text="do the check",
            sender_id="__heartbeat__",
            channel="wecom",
            peer_id="u-123",
        )
        AgentHost._dispatch_reply(
            inbound=inbound,
            result=QueryResult(text="Good morning! Any blockers today?"),
            ch=MagicMock(),
            reply_peer="u-123",
            session_key="k",
        )
        assert sent == [("u-123", "Good morning! Any blockers today?")]

    def test_heartbeat_error_is_reported_not_suppressed(self, capsys):
        # Errors during a heartbeat are a real signal — let them through.
        AgentHost._dispatch_reply(
            inbound=_heartbeat(),
            result=QueryResult(error="boom"),
            ch=None,
            reply_peer="cli-user",
            session_key="k",
        )
        out = capsys.readouterr().out
        assert "[error]" in out and "boom" in out


class TestCliReply:
    def test_user_text_terminates_line_without_reprinting(self, capsys):
        # Non-heartbeat CLI text is streamed live by ``agent_runner`` via
        # ``TextBlock`` — by the time ``_dispatch_reply`` runs, every
        # character is already on stdout. Re-printing ``result.text`` here
        # would show the reply twice. Dispatch therefore only emits a
        # terminating newline so the next ``>>>`` prompt starts on a fresh
        # line. This test locks that contract: *don't* re-print, *do*
        # produce a newline.
        AgentHost._dispatch_reply(
            inbound=_cli_user(),
            result=QueryResult(text="Hello!"),
            ch=None,
            reply_peer="cli-user",
            session_key="k",
        )
        out = capsys.readouterr().out
        assert "Hello!" not in out, (
            "dispatch must not re-print streamed text — streaming happens "
            "upstream in agent_runner"
        )
        assert out == "\n", f"expected a lone newline, got {out!r}"

    def test_user_error_reaches_stdout(self, capsys):
        AgentHost._dispatch_reply(
            inbound=_cli_user(),
            result=QueryResult(error="kaboom"),
            ch=None,
            reply_peer="cli-user",
            session_key="k",
        )
        out = capsys.readouterr().out
        assert "[error]" in out and "kaboom" in out


class TestRemoteChannel:
    def test_user_text_is_sent_via_channel(self, monkeypatch):
        sent: list[tuple[str, str]] = []

        def _fake_send(ch, peer, text):
            sent.append((peer, text))
            return True

        from pip_agent import agent_host
        monkeypatch.setattr(agent_host, "send_with_retry", _fake_send)

        AgentHost._dispatch_reply(
            inbound=_wecom_user(),
            result=QueryResult(text="hi back"),
            ch=MagicMock(),
            reply_peer="u-123",
            session_key="k",
        )
        assert sent == [("u-123", "hi back")]


class TestCronNotSilenced:
    def test_cron_text_does_not_reprint_streamed_content(self, capsys):
        # Cron inbounds go through ``run_query`` with streaming enabled (same
        # path as a regular user message — only heartbeats disable stream).
        # Dispatch therefore must NOT re-print text but must still emit a
        # trailing newline so cron output doesn't collide with the next
        # prompt. Silencing cron entirely would defeat the whole point of
        # the scheduler.
        AgentHost._dispatch_reply(
            inbound=_cron(),
            result=QueryResult(text="Daily report ready"),
            ch=None,
            reply_peer="cli-user",
            session_key="k",
        )
        out = capsys.readouterr().out
        assert "Daily report ready" not in out, (
            "cron text was streamed upstream; re-printing here duplicates it"
        )
        assert out == "\n"

    def test_cron_text_goes_through_remote_channel(self, monkeypatch):
        sent: list[tuple[str, str]] = []

        def _fake_send(ch, peer, text):
            sent.append((peer, text))
            return True

        from pip_agent import agent_host
        monkeypatch.setattr(agent_host, "send_with_retry", _fake_send)

        # Cron inbound configured for wecom.
        inbound = InboundMessage(
            text="daily",
            sender_id="__cron__",
            channel="wecom",
            peer_id="u-456",
        )
        AgentHost._dispatch_reply(
            inbound=inbound,
            result=QueryResult(text="Report"),
            ch=MagicMock(),
            reply_peer="u-456",
            session_key="k",
        )
        assert sent == [("u-456", "Report")]


class TestEmptyResult:
    @pytest.mark.parametrize("inbound", [_cli_user(), _heartbeat(), _cron()])
    def test_no_text_no_error_is_noop(self, inbound, capsys):
        AgentHost._dispatch_reply(
            inbound=inbound,
            result=QueryResult(),
            ch=None,
            reply_peer="cli-user",
            session_key="k",
        )
        assert capsys.readouterr().out == ""


class TestIsEphemeralSender:
    """Regression lock on SDK-session opt-out for scheduler senders.

    Flipping either of these to ``False`` reintroduces the bug where
    every 30 s cron tick re-ships the full user transcript to the API
    and then appends its own ``打印 hello`` back into it, turning a
    10 s cold start into a 3 min one over a day of use. If a future
    refactor needs to make cron / heartbeat stateful it MUST solve
    the transcript-bloat problem first — this test is the tripwire.
    """

    def test_cron_sender_is_ephemeral(self):
        assert _is_ephemeral_sender(_CRON_SENDER) is True

    def test_heartbeat_sender_is_ephemeral(self):
        assert _is_ephemeral_sender(_HEARTBEAT_SENDER) is True

    @pytest.mark.parametrize(
        "sender",
        ["cli-user", "wechat:alice", "wecom:bob", "", "random-string"],
    )
    def test_everything_else_keeps_session(self, sender):
        assert _is_ephemeral_sender(sender) is False


class TestReapStaleSession:
    """Regression: deleting a session JSONL must not fatal the next turn.

    Before this check, passing a dead session id into
    ``run_query(resume=...)`` made the CC subprocess exit 1, which the
    SDK surfaced as ``ClaudeSDKError: Command failed with exit code 1``
    and the user saw a fatal error the moment they typed anything after
    hand-deleting a stale JSONL (or after CC's ``/clear``). Self-heal
    contract: the id silently drops, next turn starts fresh.
    """

    def _host(self, sessions: dict[str, str]) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(_sessions=dict(sessions))

    def test_live_session_is_returned_untouched(self, monkeypatch, tmp_path):
        import pip_agent.agent_host as mod

        jsonl = tmp_path / "live.jsonl"
        jsonl.write_text("{}", "utf-8")
        monkeypatch.setattr(mod, "locate_session_jsonl", lambda sid: jsonl)
        save_calls: list[dict] = []
        monkeypatch.setattr(
            mod, "_save_sessions", lambda s: save_calls.append(dict(s)),
        )

        host = self._host({"agent:pip:cli:peer:u": "live-uuid"})
        result = AgentHost._reap_stale_session(host, "agent:pip:cli:peer:u")

        assert result == "live-uuid"
        assert host._sessions == {"agent:pip:cli:peer:u": "live-uuid"}
        assert save_calls == []  # no persistence churn on the happy path

    def test_missing_session_is_dropped_and_persisted(
        self, monkeypatch, caplog,
    ):
        import pip_agent.agent_host as mod

        monkeypatch.setattr(mod, "locate_session_jsonl", lambda sid: None)
        save_calls: list[dict] = []
        monkeypatch.setattr(
            mod, "_save_sessions", lambda s: save_calls.append(dict(s)),
        )

        host = self._host({
            "agent:pip:cli:peer:u": "dead-uuid",
            "agent:pip:cli:peer:other": "keep-uuid",
        })

        with caplog.at_level("WARNING", logger="pip_agent.agent_host"):
            result = AgentHost._reap_stale_session(host, "agent:pip:cli:peer:u")

        assert result is None
        assert host._sessions == {"agent:pip:cli:peer:other": "keep-uuid"}
        assert save_calls == [{"agent:pip:cli:peer:other": "keep-uuid"}]
        assert any(
            "missing on disk" in rec.message for rec in caplog.records
        )

    def test_no_session_in_map_is_noop(self, monkeypatch):
        import pip_agent.agent_host as mod

        def _boom(_sid):  # locate must not be called if there's no id
            raise AssertionError("locate_session_jsonl should be skipped")

        monkeypatch.setattr(mod, "locate_session_jsonl", _boom)
        save_calls: list[dict] = []
        monkeypatch.setattr(
            mod, "_save_sessions", lambda s: save_calls.append(dict(s)),
        )

        host = self._host({})
        result = AgentHost._reap_stale_session(host, "agent:pip:cli:peer:u")

        assert result is None
        assert save_calls == []


class TestSessionLockMap:
    """Regression: same-session messages must serialize, different sessions
    must not interfere.

    The group-chat failure mode the lock fixes: members A and B reply to
    the bot at the same instant; both resolve to the same
    ``agent:pip:wecom:peer:<gid>`` session key; their turns interleave;
    both resume the same SDK ``session_id``; the one that writes back
    second wins and the other's turn is silently lost.

    Testing the full interleave is expensive; what we can cheaply lock
    in is the *mechanism*: two requests for the same key get the same
    lock instance, two requests for different keys get distinct locks,
    and the lock dict doesn't explode under repeated hits on the same
    key. Actual mutual exclusion is an ``asyncio.Lock`` guarantee —
    not ours to re-prove.
    """

    def _host(self):
        from types import SimpleNamespace

        return SimpleNamespace(_session_locks={})

    def test_same_key_returns_the_same_lock(self):
        host = self._host()
        a = AgentHost._get_session_lock(host, "sk-1")
        b = AgentHost._get_session_lock(host, "sk-1")
        assert a is b

    def test_different_keys_get_distinct_locks(self):
        host = self._host()
        a = AgentHost._get_session_lock(host, "sk-1")
        b = AgentHost._get_session_lock(host, "sk-2")
        assert a is not b

    def test_lock_dict_does_not_grow_on_repeat(self):
        host = self._host()
        for _ in range(10):
            AgentHost._get_session_lock(host, "sk-1")
        assert len(host._session_locks) == 1

    def test_lock_is_an_asyncio_lock(self):
        import asyncio

        host = self._host()
        lock = AgentHost._get_session_lock(host, "sk-1")
        assert isinstance(lock, asyncio.Lock)
