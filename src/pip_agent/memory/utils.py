"""Shared utilities for the memory pipeline."""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

_FENCE_OPEN_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n?")
_FENCE_CLOSE_RE = re.compile(r"\n?\s*```\s*$")


def extract_json_array(text: str) -> list | None:
    """Extract a JSON array from LLM output, tolerating markdown fences.

    Returns the parsed list on success, or ``None`` if no valid array is found.

    Strategy (escalating tolerance):

    1. Direct ``json.loads`` — the prompt contract. The LLM is asked to
       return only a JSON array.
    2. Strip ```` ```json ... ``` ```` markdown fences and retry.
    3. Scan for the first ``[`` and let ``json.JSONDecoder().raw_decode``
       stop at the matching ``]`` — this correctly handles nested
       brackets and ignores whatever the model chattered after the
       array, which a greedy regex like ``r"\\[.*\\]"`` would over-match
       across (e.g. a second stray array in a trailing "note to self").
    """
    text = text.strip()

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    stripped = _FENCE_OPEN_RE.sub("", text)
    stripped = _FENCE_CLOSE_RE.sub("", stripped).strip()
    if stripped != text:
        try:
            result = json.loads(stripped)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # raw_decode consumes the longest valid JSON prefix starting at the
    # given offset and reports where parsing stopped — we use it to pick
    # out a JSON array embedded in prose without relying on a regex that
    # can either miss nested brackets or greedily span multiple arrays.
    decoder = json.JSONDecoder()
    for candidate in (text, stripped):
        start = candidate.find("[")
        if start < 0:
            continue
        try:
            result, _end = decoder.raw_decode(candidate[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(result, list):
            return result

    log.warning("extract_json_array: no valid JSON array found in: %.200s", text)
    return None
