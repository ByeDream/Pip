"""Tiered model registry — single source of truth for model name resolution.

Three tiers (t0 - t1 - t2) live in the env (``MODEL_T0`` / ``MODEL_T1`` /
``MODEL_T2``). Every call site picks a tier; never a concrete model name.
Async / background tasks (heartbeat, cron, reflect, dream) are pinned to
fixed tiers in code so they cannot accidentally burn the strongest model
on cheap work.

Failures on a specific model degrade DOWN the chain (never up):

* ``t0`` -> ``[model_t0, model_t1, model_t2]``
* ``t1`` -> ``[model_t1, model_t2]``
* ``t2`` -> ``[model_t2]``

Empty env entries are skipped, so a partly-configured ``.env`` (e.g. only
``MODEL_T2`` set) still works for the tiers that do have a name.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal, TypeVar

log = logging.getLogger(__name__)


Tier = Literal["t0", "t1", "t2"]

DEFAULT_TIER: Tier = "t0"

VALID_TIERS: frozenset[str] = frozenset({"t0", "t1", "t2"})


# Task -> tier map. Hard-coded on purpose: the design intent is that an
# async/cheap stage cannot be bent to use the strongest model. The
# persona-driven main turn is the only stage whose tier varies at runtime,
# and it does so via :attr:`AgentConfig.tier` rather than this table.
TASK_TIER: dict[str, Tier] = {
    "heartbeat": "t2",
    "cron": "t2",
    "reflect": "t1",
    "consolidate": "t1",
    "axioms": "t0",
}


_DOWN_CHAIN: dict[Tier, tuple[str, ...]] = {
    "t0": ("model_t0", "model_t1", "model_t2"),
    "t1": ("model_t1", "model_t2"),
    "t2": ("model_t2",),
}


def resolve_chain(tier: Tier) -> list[str]:
    """Return the ordered list of concrete model names to try for ``tier``.

    Empty / whitespace entries are skipped. The result may be empty when
    the env is not configured for ``tier`` or any lower tier; callers
    should treat that as "no model available, skip this LLM call".
    """
    from pip_agent.config import settings

    chain: list[str] = []
    for attr in _DOWN_CHAIN[tier]:
        value = (getattr(settings, attr, "") or "").strip()
        if value:
            chain.append(value)
    return chain


def primary_model(tier: Tier) -> str:
    """First model name from :func:`resolve_chain`; empty if none configured.

    Used where a single concrete name is needed up front (e.g. the
    ``{model_name}`` template substitution in persona system prompts).
    """
    chain = resolve_chain(tier)
    return chain[0] if chain else ""


# Substrings that indicate "this specific model name is not available on
# the server we just called" — case-insensitive match against the
# exception's string form. Anything outside this list (rate limit,
# network, auth) does NOT trigger a downgrade because switching models
# would not help.
_MODEL_INVALID_MARKERS: tuple[str, ...] = (
    "model not found",
    "model_not_found",
    "model does not exist",
    "model_does_not_exist",
    "invalid model",
    "unknown model",
    "model is not supported",
    "no such model",
    "not_found_error",
)


def is_model_invalid_error(exc: BaseException) -> bool:
    """True when ``exc`` indicates the requested model name is unusable.

    Conservative by design: anything we cannot positively recognise as a
    model-availability issue stays a hard error so we don't silently mask
    auth / network / quota problems by burning through tier candidates.
    """
    msg = str(exc).lower()
    if any(marker in msg for marker in _MODEL_INVALID_MARKERS):
        return True
    # Typed 404 from the Anthropic SDK with "model" in the body — treat as
    # model-invalid; the bare 404 with no model context is left alone.
    name = type(exc).__name__.lower()
    if "notfound" in name and "model" in msg:
        return True
    return False


T = TypeVar("T")


def with_model_fallback(
    tier: Tier,
    call: Callable[[str], T],
    *,
    label: str = "",
) -> T:
    """Run ``call(model_name)`` over the resolved tier chain.

    Each candidate model is passed to ``call`` in turn. If ``call`` raises
    and :func:`is_model_invalid_error` matches, we move to the next
    candidate; any other exception re-raises immediately.

    Raises ``RuntimeError`` when the chain is empty (caller should treat
    that as "no model configured" and skip). Raises the final candidate's
    exception when every candidate fails with a model-invalid error.
    """
    chain = resolve_chain(tier)
    if not chain:
        raise RuntimeError(
            f"No model configured for tier {tier}. "
            "Set MODEL_T0 / MODEL_T1 / MODEL_T2 in .env.",
        )

    last_exc: BaseException | None = None
    for idx, model in enumerate(chain):
        try:
            return call(model)
        except BaseException as exc:  # noqa: BLE001
            if not is_model_invalid_error(exc):
                raise
            last_exc = exc
            tag = f" [{label}]" if label else ""
            log.warning(
                "model%s tier=%s candidate %d/%d (%s) rejected as invalid; "
                "falling back: %s",
                tag, tier, idx + 1, len(chain), model, exc,
            )
    assert last_exc is not None
    raise last_exc
