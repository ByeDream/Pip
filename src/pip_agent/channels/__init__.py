"""Channel abstraction layer for multi-platform messaging.

Provides a unified :class:`InboundMessage` type and :class:`Channel`
ABC so the agent loop can receive/send messages through CLI, WeChat
(iLink Bot protocol), or WeCom (企业微信智能机器人 WebSocket SDK)
without platform-specific logic.

Historically this module lived in a single 1300-line ``channels.py``.
The concrete channels now live in dedicated submodules
(:mod:`pip_agent.channels.cli`, :mod:`pip_agent.channels.wechat`,
:mod:`pip_agent.channels.wecom`) while the base ABC and the transport
helpers stay in :mod:`pip_agent.channels.base`.

Tier 3 cold-start note
----------------------
Historically this ``__init__`` eagerly imported *every* channel, which
in turn dragged in ``aibot`` (WeCom SDK, ~450 ms) and the whole
``aiohttp`` dependency graph (~180 ms). CLI mode never needed those,
yet paid the import cost on every launch.

We now lazy-load :class:`WeChatChannel`, :class:`WecomChannel`, and
their blocking poll / ws loops via ``__getattr__``. Call sites
that only touch CLI (``CLIChannel``, :class:`InboundMessage`, base
helpers) skip the heavy imports entirely; anything that *names*
wechat / wecom symbols triggers the real import on first access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pip_agent.channels.base import (
    BACKOFF_SCHEDULE,
    Attachment,
    Channel,
    ChannelManager,
    InboundMessage,
    _detect_image_mime,
    send_with_retry,
)
from pip_agent.channels.cli import CLIChannel

__all__ = [
    "BACKOFF_SCHEDULE",
    "Attachment",
    "CLIChannel",
    "Channel",
    "ChannelManager",
    "InboundMessage",
    "WeChatChannel",
    "WecomChannel",
    "_detect_image_mime",
    "send_with_retry",
    "wechat_poll_loop",
    "wecom_ws_loop",
]

if TYPE_CHECKING:  # pragma: no cover - type-only imports
    from pip_agent.channels.wechat import WeChatChannel, wechat_poll_loop
    from pip_agent.channels.wecom import WecomChannel, wecom_ws_loop


_LAZY_MAP = {
    "WeChatChannel": ("pip_agent.channels.wechat", "WeChatChannel"),
    "wechat_poll_loop": ("pip_agent.channels.wechat", "wechat_poll_loop"),
    "WecomChannel": ("pip_agent.channels.wecom", "WecomChannel"),
    "wecom_ws_loop": ("pip_agent.channels.wecom", "wecom_ws_loop"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_MAP.get(name)
    if target is None:
        raise AttributeError(f"module 'pip_agent.channels' has no attribute {name!r}")

    import importlib

    mod = importlib.import_module(target[0])
    obj = getattr(mod, target[1])
    # Cache on the package module so subsequent lookups skip __getattr__.
    globals()[name] = obj
    return obj
