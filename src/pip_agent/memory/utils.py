"""Shared utilities for the memory pipeline."""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def extract_json_array(text: str) -> list | None:
    """Extract a JSON array from LLM output, tolerating markdown fences.

    Returns the parsed list on success, or ``None`` if no valid array is found.
    """
    text = text.strip()

    # Fast path: direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Strip markdown fences (```json ... ```, with optional leading whitespace)
    stripped = re.sub(
        r"^\s*```[a-zA-Z]*\s*\n?", "", text,
    )
    stripped = re.sub(r"\n?\s*```\s*$", "", stripped).strip()
    if stripped != text:
        try:
            result = json.loads(stripped)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Last resort: regex for outermost [ ... ]
    m = _JSON_ARRAY_RE.search(text)
    if m:
        try:
            result = json.loads(m.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    log.warning("extract_json_array: no valid JSON array found in: %.200s", text)
    return None
