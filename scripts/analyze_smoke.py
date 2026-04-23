"""Smoke-test analyser.

Reads ``profile.jsonl`` (or any JSONL passed on argv) and prints a
human-readable rollup focused on the metrics we care about for Tier
1+2+3 verification:

* cold-start milestones (``cold_start.*``)
* per-turn breakdown (``turn.open``, ``stream.session_init``,
  ``stream.first_text``, ``stream.result``, ``stream.turn`` span)
* batching events (``host.batch_coalesced``)
* streaming-session lifecycle (``stream.opened`` / ``reused`` /
  ``closed`` / ``stale_*`` / ``idle_sweep``)
* errors (``stream.sdk_error``, ``stream.create_failed``)

Usage::

    python analyze_smoke.py [path.jsonl]

Defaults to ``D:/Workspace/pip-test/profile-logs/profile.jsonl``.
"""
from __future__ import annotations

import io
import json
import sys
from collections import defaultdict
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True,
    )

DEFAULT_PATH = Path(r"D:/Workspace/pip-test/profile-logs/profile.jsonl")


def _meta(e: dict) -> dict:
    m = e.get("meta")
    return m if isinstance(m, dict) else {}


def _evt(e: dict) -> str:
    return e.get("evt") or ""


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH
    if not path.exists():
        print(f"[err] not found: {path}")
        return 2

    events: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    print(f"=== {path}  ({len(events)} events) ===\n")

    # -------- cold start --------
    # Deltas between adjacent milestones make the tall bars obvious:
    # a small since_start_ms can still hide a 400 ms step right before
    # it. Ordering by ``since_start_ms`` is safe because each milestone
    # is emitted in the order run_host executes; we don't have
    # interleaved processes to worry about.
    print("--- cold_start ---")
    cs = [e for e in events if _evt(e).startswith("cold_start.")]
    cs.sort(key=lambda e: _meta(e).get("since_start_ms") or 0.0)
    prev_ms: float | None = None
    for e in cs:
        m = _meta(e)
        ms = float(m.get("since_start_ms") or 0.0)
        delta = f"(+{ms - prev_ms:>7.1f} ms)" if prev_ms is not None else "           "
        extra_bits = []
        for key in ("mode", "agents", "channels", "logged_in"):
            if key in m:
                extra_bits.append(f"{key}={m[key]}")
        extra = ("  " + "  ".join(extra_bits)) if extra_bits else ""
        print(f"  {ms:>8.1f} ms  {delta}  {_evt(e)}{extra}")
        prev_ms = ms
    print()

    # -------- batching --------
    print("--- Tier 2: text batching ---")
    batches = [e for e in events if _evt(e) == "host.batch_coalesced"]
    if batches:
        total_fused = sum(_meta(b).get("fused", 0) for b in batches)
        total_after = sum(_meta(b).get("after", 0) for b in batches)
        total_before = sum(_meta(b).get("before", 0) for b in batches)
        print(
            f"  {len(batches)} coalesce event(s); "
            f"{total_before} -> {total_after} inbounds (saved {total_fused} LLM turns)"
        )
        for b in batches:
            m = _meta(b)
            print(
                f"    before={m.get('before')} after={m.get('after')} "
                f"fused={m.get('fused')}"
            )
    else:
        print("  (none — no contiguous text-only inbounds in same drain tick)")
    print()

    # -------- streaming session lifecycle --------
    print("--- Tier 1: streaming session lifecycle ---")
    by_evt = defaultdict(list)
    for e in events:
        ev = _evt(e)
        if ev.startswith("stream."):
            by_evt[ev].append(e)
    interesting = [
        "stream.opened", "stream.reused", "stream.closed",
        "stream.idle_sweep", "stream.stale_detected",
        "stream.stale_recovered", "stream.create_failed",
        "stream.sdk_error",
    ]
    for ev in interesting:
        items = by_evt.get(ev, [])
        if not items and ev not in ("stream.opened", "stream.reused"):
            continue
        sks = sorted({_meta(e).get("session_key") for e in items if _meta(e).get("session_key")})
        sk_repr = ", ".join(sk for sk in sks if sk) or "-"
        print(f"  {ev:<28} count={len(items):<3}  session_keys=[{sk_repr}]")
    print()

    # -------- per-turn drilldown --------
    print("--- per-turn drilldown ---")
    # Group by session_key + turn number; also collect span.close for stream.turn.
    turns: dict[tuple[str, int], dict[str, float]] = defaultdict(dict)
    for e in events:
        ev = _evt(e)
        m = _meta(e)
        if ev in ("stream.session_init", "stream.first_text", "stream.result", "stream.user_pushed"):
            sk = m.get("session_key", "?")
            tn = int(m.get("turn") or 0)
            if ev == "stream.session_init":
                turns[(sk, tn)]["init_ms"] = float(m.get("since_stream_ms") or 0.0)
            elif ev == "stream.first_text":
                turns[(sk, tn)]["first_text_ms"] = float(m.get("since_stream_ms") or 0.0)
            elif ev == "stream.result":
                turns[(sk, tn)]["result_ms"] = float(m.get("since_stream_ms") or 0.0)
            elif ev == "stream.user_pushed":
                turns[(sk, tn)]["push_text_len"] = float(m.get("text_len") or 0.0)
        if ev == "span.close" and e.get("name") == "stream.turn":
            sk = m.get("session_key", "?")
            tn = int(m.get("turn") or 0)
            turns[(sk, tn)]["span_dur_ms"] = float(e.get("dur_ms") or 0.0)

    if turns:
        # Stable order: session_key then turn number.
        keys = sorted(turns.keys())
        # Group by session_key for readability.
        last_sk = None
        for (sk, tn) in keys:
            if sk != last_sk:
                print(f"\n  session_key = {sk}")
                last_sk = sk
            d = turns[(sk, tn)]
            init = d.get("init_ms")
            ftxt = d.get("first_text_ms")
            res = d.get("result_ms")
            span = d.get("span_dur_ms")
            push_len = d.get("push_text_len")
            push_str = f"  text_len={int(push_len)}" if push_len is not None else ""
            print(
                f"    turn {tn:>2}: "
                f"init={(init or 0):>6.1f} ms  "
                f"first_text={(ftxt or 0):>7.1f} ms  "
                f"result={(res or 0):>7.1f} ms  "
                f"span={(span or 0):>7.1f} ms{push_str}"
            )
    else:
        print("  (no streaming turns observed)")
    print()

    # -------- one-shot (legacy) path --------
    legacy = [e for e in events if _evt(e) == "host.session_preflight"]
    if legacy:
        # only count the ones that actually went through run_query (no
        # streaming branch). Easy heuristic: look for ``runner.query.*``
        # spans that are NOT inside a stream.turn span.
        runner_calls = [
            e for e in events
            if e.get("name") in ("runner.query", "runner.query.cold")
            and _evt(e) == "span.close"
        ]
        print("--- legacy run_query (one-shot) path ---")
        print(f"  preflights={len(legacy)}  runner.query closes={len(runner_calls)}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
