"""Channel abstraction layer for multi-platform messaging.

Provides a unified :class:`InboundMessage` type and :class:`Channel`
ABC so the agent loop can receive/send messages through CLI, WeChat
(iLink Bot protocol), or WeCom (企业微信智能机器人 WebSocket SDK)
without platform-specific logic.

Historically this module lived in a single 1300-line ``channels.py``.
The concrete channels now live in dedicated submodules
(:mod:`pip_agent.channels.cli`, :mod:`pip_agent.channels.wechat`,
:mod:`pip_agent.channels.wecom`) while the base ABC and the transport
helpers stay in :mod:`pip_agent.channels.base`. This package's
``__init__`` re-exports every public symbol so existing call sites
(``from pip_agent.channels import InboundMessage``) keep working.
"""
from __future__ import annotations

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
from pip_agent.channels.wechat import WeChatChannel, wechat_poll_loop
from pip_agent.channels.wecom import WecomChannel, wecom_ws_loop

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
