"""Ad-hoc: analyze real-world inter-arrival gaps from a WeCom/WeChat session."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(path: str) -> None:
    events = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    start = events[0]["mono_ns"]

    inbounds: list[dict] = []
    channel = None
    for e in events:
        ev = e.get("evt", "")
        if ev in ("wecom.inbound_received", "wechat.inbound_received", "cli.inbound_received"):
            ms = (e["mono_ns"] - start) / 1e6
            meta = e.get("meta", {})
            channel = ev.split(".")[0]
            inbounds.append({"ms": ms, "text_len": meta.get("text_len", 0)})

    if not inbounds:
        print("no inbounds found")
        return

    results_ms = sorted((e["mono_ns"] - start) / 1e6 for e in events if e.get("evt") == "stream.result")
    pushes_ms = sorted((e["mono_ns"] - start) / 1e6 for e in events if e.get("evt") == "stream.user_pushed")

    active: list[tuple[float, float]] = []
    ri = 0
    for p in pushes_ms:
        while ri < len(results_ms) and results_ms[ri] < p:
            ri += 1
        if ri >= len(results_ms):
            break
        active.append((p, results_ms[ri]))
        ri += 1

    def in_active(ms: float) -> bool:
        return any(s <= ms <= e for s, e in active)

    free = [ib for ib in inbounds if not in_active(ib["ms"])]
    busy = [ib for ib in inbounds if in_active(ib["ms"])]

    print(f"=== {channel} session ({len(events)} events) ===")
    print(f"Total inbounds: {len(inbounds)}")
    print(f"  arrived-when-BUSY (captured by lock-time, parked): {len(busy)}")
    print(f"  arrived-when-FREE (escaped, started own turn):    {len(free)}")
    print()

    gaps = [inbounds[i]["ms"] - inbounds[i - 1]["ms"] for i in range(1, len(inbounds))]
    gs = sorted(gaps)

    def pct(a: list[float], p: int) -> float:
        i = min(len(a) - 1, int(len(a) * p / 100))
        return a[i]

    print("Inter-arrival gaps (ms) across ALL consecutive inbound pairs:")
    print(f"  min={gs[0]:.0f}  p25={pct(gs, 25):.0f}  p50={pct(gs, 50):.0f}  p75={pct(gs, 75):.0f}  p90={pct(gs, 90):.0f}  max={gs[-1]:.0f}")
    print()

    escaped_idx = [i for i, ib in enumerate(inbounds) if not in_active(ib["ms"])]

    catchable = {500: 0, 800: 0, 1000: 0, 1500: 0, 2000: 0, 3000: 0, 5000: 0}

    print("Escaped msgs (started own turn) and their forward-gap to the next inbound:")
    print("  [CATCH@N] = would be captured if pre-flight window >= N ms")
    print()
    for i in escaped_idx:
        ib = inbounds[i]
        if i < len(inbounds) - 1:
            fg = inbounds[i + 1]["ms"] - ib["ms"]
            hit = []
            for t in sorted(catchable.keys()):
                if fg <= t:
                    catchable[t] += 1
                    hit.append(str(t))
            tags = ",".join(hit) if hit else "-"
            verdict = (
                "CONTINUATION"
                if fg < 2000
                else ("mid-gap" if fg < 5000 else ("reply-gap" if fg < 15000 else "NEW-TOPIC"))
            )
            print(f"  msg#{i:2d}  len={ib['text_len']:3d}  ->next={fg:7.0f}ms  [{verdict}]  catch@=[{tags}]")
        else:
            print(f"  msg#{i:2d}  len={ib['text_len']:3d}  ->next=(last msg)  [session-end]")

    print()
    print("Pre-flight window effectiveness (among escaped msgs):")
    N_escaped = len(escaped_idx)
    for t in sorted(catchable.keys()):
        caught = catchable[t]
        print(f"  window={t:5d}ms  catches {caught:2d}/{N_escaped} escaped msgs  ({100 * caught / max(N_escaped,1):.0f}%)")

    # Wall-clock cost estimate of the window
    print()
    print("Cost of pre-flight window (added latency per ISOLATED msg):")
    print("  window=500ms   -> +0.5s per first-in-burst / per isolated msg")
    print("  window=1000ms  -> +1.0s")
    print("  window=1500ms  -> +1.5s (user perceives as noticeable)")
    print("  window=2000ms  -> +2.0s (user will likely notice)")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "D:/Workspace/pip-test/profile-logs/profile.jsonl"
    main(path)
