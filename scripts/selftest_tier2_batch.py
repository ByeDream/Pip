"""Unit-style self-test for the Tier 2 text-batching helpers.

Tier 2 intentionally lives as a pure function (``_coalesce_text_inbounds``)
on ``agent_host`` so it can be exercised without spinning up the SDK
subprocess. These cases capture the policy matrix the config docstring
promises:

* same-conversation text bubbles fuse;
* attachments break the run;
* scheduler payloads (``source_job_id``) never fuse;
* host slash commands (``/exit``, ``/flush``) never fuse;
* different senders / peers never cross the stream.

Run::

    D:\\Workspace\\pip-test\\.venv\\Scripts\\python.exe \\
        D:\\Workspace\\Pip-Boy\\scripts\\selftest_tier2_batch.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

# cp1252 console + non-ASCII assertions don't mix; force UTF-8 stdout.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True,
    )

# Expose the source tree before importing.
SRC = Path(r"D:\Workspace\Pip-Boy\src")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pip_agent.agent_host import _coalesce_text_inbounds  # noqa: E402
from pip_agent.channels.base import Attachment, InboundMessage  # noqa: E402


def _m(text: str, sender: str = "u1", peer: str = "p1", **kw: object) -> InboundMessage:
    return InboundMessage(
        text=text,
        sender_id=sender,
        channel=str(kw.get("channel", "wecom")),
        peer_id=peer,
        guild_id=str(kw.get("guild_id", "")),
        account_id="bot1",
        is_group=bool(kw.get("is_group", False)),
        agent_id=str(kw.get("agent_id", "")),
        raw={},
        attachments=list(kw.get("attachments") or []),
        source_job_id=str(kw.get("source_job_id", "")),
    )


def _check(label: str, cond: bool, detail: str) -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}: {detail}")
    return cond


def main() -> int:
    ok = True

    # Case A — three same-conversation text bubbles fuse into one.
    a = [_m("早"), _m("今天提醒我开会"), _m("谢谢")]
    out, fused = _coalesce_text_inbounds(a, "\n\n")
    ok &= _check(
        "A fuse 3 text bubbles",
        len(out) == 1 and fused == 2 and out[0].text == "早\n\n今天提醒我开会\n\n谢谢",
        f"len={len(out)} fused={fused} text={out[0].text!r}",
    )

    # Case B — attachment breaks the run. First fuses with nothing, second
    # carries an image, third stays discrete (adjacent to image-bearing).
    img = Attachment(type="image", data=b"\xff\xd8\xff\xe0", mime_type="image/jpeg")
    b = [_m("先看图"), _m("", attachments=[img]), _m("解释下")]
    out, fused = _coalesce_text_inbounds(b, "\n\n")
    ok &= _check(
        "B attachment stays separate",
        len(out) == 3 and fused == 0,
        f"len={len(out)} fused={fused}",
    )

    # Case C — scheduler payload never fuses into a user text.
    c = [_m("早"), _m("heartbeat tick", sender="heartbeat", source_job_id="hb:1")]
    out, fused = _coalesce_text_inbounds(c, "\n\n")
    ok &= _check(
        "C scheduler payload not fused",
        len(out) == 2 and fused == 0,
        f"len={len(out)} fused={fused}",
    )

    # Case D — slash commands stay discrete even when adjacent to plain text.
    d = [_m("checkpoint note"), _m("/flush"), _m("ok done")]
    out, fused = _coalesce_text_inbounds(d, "\n\n")
    ok &= _check(
        "D slash commands stay discrete",
        len(out) == 3 and fused == 0,
        f"len={len(out)} fused={fused}",
    )

    # Case E — different peers never fuse.
    e = [_m("hi", peer="p1"), _m("hi", peer="p2")]
    out, fused = _coalesce_text_inbounds(e, "\n\n")
    ok &= _check(
        "E different peers stay separate",
        len(out) == 2 and fused == 0,
        f"len={len(out)} fused={fused}",
    )

    # Case F — group vs DM from same sender stay separate (is_group differs).
    f = [_m("hi", peer="grp1"), _m("hi", peer="grp1", is_group=True, guild_id="grp1")]
    out, fused = _coalesce_text_inbounds(f, "\n\n")
    ok &= _check(
        "F DM vs group stay separate",
        len(out) == 2 and fused == 0,
        f"len={len(out)} fused={fused}",
    )

    # Case G — interleaved: same-peer pair + foreign msg + resume same peer.
    g = [_m("a1"), _m("a2"), _m("b1", peer="p2"), _m("a3")]
    out, fused = _coalesce_text_inbounds(g, "|")
    ok &= _check(
        "G non-contiguous peer A does not fuse across peer B",
        len(out) == 3 and fused == 1 and out[0].text == "a1|a2" and out[2].text == "a3",
        f"len={len(out)} fused={fused} texts={[o.text for o in out]}",
    )

    # Case H — empty-text inbound should not block fusing of neighbours.
    h = [_m("hi"), _m(""), _m("there")]
    out, fused = _coalesce_text_inbounds(h, "|")
    ok &= _check(
        "H empty text is not a fuse anchor",
        len(out) == 3,
        f"len={len(out)} fused={fused}",
    )

    print("\nOVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
