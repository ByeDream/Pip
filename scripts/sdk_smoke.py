"""SDK contract smoke test for claude-agent-sdk.

Goal: verify the exact field contracts of ClaudeAgentOptions / HookMatcher /
PreCompactHookInput / StopHookInput / SystemMessage(init) and observe
~/.claude/projects/<cwd-encoded>/<session>.jsonl layout so Phase 4.5's
memory/transcript_source.py can rely on concrete facts, not guesses.

Run with `python scripts/sdk_smoke.py`. The script never errors out; it always
prints what it observed so we have something to paste into docs/sdk-contract-notes.md.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
import typing
from pathlib import Path


def _print_header(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def inspect_module() -> None:
    _print_header("module meta")
    import claude_agent_sdk as m

    print(f"version   : {getattr(m, '__version__', 'unknown')}")
    print(f"location  : {m.__file__}")


def inspect_dataclass(name: str, cls) -> None:
    _print_header(name)
    try:
        fields = dataclasses.fields(cls)
    except TypeError:
        print(f"{name} is not a dataclass: {cls!r}")
        hints = typing.get_type_hints(cls)
        for k, v in hints.items():
            print(f"  {k}: {v}")
        return
    for f in fields:
        default = "<required>" if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING else (
            f"factory={f.default_factory.__name__}" if f.default is dataclasses.MISSING else repr(f.default)
        )
        print(f"  {f.name}: {f.type}  ({default})")


def inspect_typeddict(name: str, cls) -> None:
    _print_header(name)
    try:
        hints = typing.get_type_hints(cls)
    except Exception as exc:  # noqa: BLE001
        print(f"could not resolve hints for {name}: {exc!r}")
        print(f"raw __annotations__ = {getattr(cls, '__annotations__', {})}")
        return
    for k, v in hints.items():
        print(f"  {k}: {v}")


def list_cc_projects() -> None:
    _print_header("~/.claude/projects listing")
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        print(f"(not present) {root}")
        return
    print(f"root: {root}")
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            jsonls = sorted(entry.glob("*.jsonl"))
            print(f"  {entry.name}   ({len(jsonls)} session(s))")
            for j in jsonls[:3]:
                print(f"    - {j.name}  [{j.stat().st_size} bytes]")


def peek_jsonl(limit: int = 3) -> None:
    _print_header("JSONL schema peek (first 3 lines of most recent session)")
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        print("(no projects yet — run the live query step below, then re-run this)")
        return
    candidates: list[Path] = []
    for entry in root.iterdir():
        if entry.is_dir():
            candidates.extend(entry.glob("*.jsonl"))
    if not candidates:
        print("(no .jsonl files yet)")
        return
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    print(f"file: {latest}")
    print(f"size: {latest.stat().st_size} bytes")
    with latest.open("r", encoding="utf-8", errors="replace") as fh:
        for idx, line in enumerate(fh):
            if idx >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"  line {idx}: invalid json: {exc}")
                continue
            print(f"--- line {idx} top-level keys: {sorted(payload.keys())}")
            if "message" in payload and isinstance(payload["message"], dict):
                print(f"    message keys: {sorted(payload['message'].keys())}")
                content = payload["message"].get("content")
                if isinstance(content, list) and content:
                    print(f"    first content block keys: {sorted(content[0].keys()) if isinstance(content[0], dict) else type(content[0]).__name__}")


async def live_query_probe() -> dict:
    """Run one short query to observe SystemMessage(init), PreCompact hook behavior."""
    _print_header("live query (requires claude CLI + auth)")

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        HookContext,
        HookMatcher,
        SystemMessage,
        query,
    )

    observed: dict = {"init": None, "precompact_calls": [], "stop_calls": [], "messages": []}

    async def pre_compact_hook(input_data, tool_use_id, context: HookContext):
        observed["precompact_calls"].append({
            "input_data_keys": sorted(input_data.keys()) if isinstance(input_data, dict) else None,
            "input_data": input_data,
            "tool_use_id": tool_use_id,
            "context_repr": repr(context),
        })
        return {}

    async def stop_hook(input_data, tool_use_id, context: HookContext):
        observed["stop_calls"].append({
            "input_data_keys": sorted(input_data.keys()) if isinstance(input_data, dict) else None,
            "input_data": input_data,
        })
        return {}

    opts_kwargs = dict(
        system_prompt="You are a terse assistant for a smoke test. Reply 'pong'.",
        permission_mode="bypassPermissions",
        hooks={
            "PreCompact": [HookMatcher(hooks=[pre_compact_hook])],
            "Stop": [HookMatcher(hooks=[stop_hook])],
        },
    )

    for extra_key, extra_val in [
        ("setting_sources", ["project", "user"]),
    ]:
        try:
            opts = ClaudeAgentOptions(**opts_kwargs, **{extra_key: extra_val})
            print(f"ClaudeAgentOptions accepted '{extra_key}={extra_val!r}'")
            break
        except TypeError as exc:
            print(f"ClaudeAgentOptions rejected '{extra_key}': {exc}")
            opts = ClaudeAgentOptions(**opts_kwargs)
    else:
        opts = ClaudeAgentOptions(**opts_kwargs)

    try:
        async for msg in query(prompt="say 'pong' and nothing else", options=opts):
            observed["messages"].append({"type": type(msg).__name__, "repr": repr(msg)[:400]})
            if isinstance(msg, SystemMessage):
                print(f"SystemMessage subtype={getattr(msg, 'subtype', None)}  data keys={sorted(getattr(msg, 'data', {}).keys()) if isinstance(getattr(msg, 'data', None), dict) else None}")
                if getattr(msg, "subtype", None) == "init":
                    observed["init"] = getattr(msg, "data", None)
            elif isinstance(msg, AssistantMessage):
                print(f"AssistantMessage content blocks: {[type(b).__name__ for b in (msg.content or [])]}")
    except Exception as exc:  # noqa: BLE001
        print(f"!! live query failed: {exc!r}")
        observed["error"] = repr(exc)

    return observed


def main() -> int:
    import claude_agent_sdk as sdk
    inspect_module()

    inspect_dataclass("ClaudeAgentOptions", sdk.ClaudeAgentOptions)
    inspect_dataclass("HookMatcher", sdk.HookMatcher)
    inspect_typeddict("PreCompactHookInput", sdk.PreCompactHookInput)
    inspect_typeddict("StopHookInput", sdk.StopHookInput)
    inspect_typeddict("PreToolUseHookInput", sdk.PreToolUseHookInput)
    inspect_typeddict("PostToolUseHookInput", sdk.PostToolUseHookInput)

    _print_header("SettingSource literal values")
    print(sdk.SettingSource)
    print(getattr(sdk.SettingSource, "__args__", None))

    list_cc_projects()

    if "--skip-live" in sys.argv:
        print("\n(skipping live query)")
        return 0

    print("\n(attempting live query — set PIP_SMOKE_SKIP_LIVE=1 to skip)")
    if os.environ.get("PIP_SMOKE_SKIP_LIVE"):
        print("skipped per env")
        return 0
    observed = asyncio.run(live_query_probe())
    _print_header("init session info observed")
    print(json.dumps(observed.get("init"), indent=2, default=str))
    _print_header("messages observed")
    for m in observed["messages"]:
        print(f"  {m['type']}: {m['repr']}")
    _print_header("precompact hook calls")
    print(json.dumps(observed["precompact_calls"], indent=2, default=str))
    _print_header("stop hook calls")
    print(json.dumps(observed["stop_calls"], indent=2, default=str))

    peek_jsonl()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
