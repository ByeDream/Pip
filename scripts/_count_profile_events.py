"""Quick: count and list event names in a profile jsonl."""
import json
import sys
from collections import Counter

path = sys.argv[1] if len(sys.argv) > 1 else r"D:\Workspace\pip-test\profile-logs\profile.jsonl"
names = []
stream_events = []
with open(path, "r", encoding="utf-8", errors="replace") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Instant events use ``evt`` as the name; span open/close use
        # ``evt: span.open / span.close`` with the span label in ``name``.
        evt = e.get("evt")
        if evt in ("span.open", "span.close"):
            n = e.get("name") or evt
        else:
            n = evt or e.get("name") or "?"
        names.append(n)
        if isinstance(n, str) and n.startswith("stream."):
            stream_events.append(e)

print(f"TOTAL events: {len(names)}")
print("top event names:")
for name, count in Counter(names).most_common(25):
    print(f"  {count:4d}  {name}")

print(f"\nstream.* events: {len(stream_events)}")
for e in stream_events[:20]:
    # Trim fields for readability
    meta = e.get("meta") or {}
    slim = {
        "evt": e.get("evt"),
        "name": e.get("name"),
        "dur_ms": e.get("dur_ms"),
        **{k: meta.get(k) for k in ("session_key", "turn", "since_stream_ms", "sid", "err", "reason", "mode") if k in meta},
    }
    slim = {k: v for k, v in slim.items() if v is not None}
    print("  ", slim)
