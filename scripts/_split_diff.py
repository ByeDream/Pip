"""Extract a subset of @@-hunks from a git diff so the result is a
valid patch ``git apply --cached`` will accept.

Usage::

    python _split_diff.py <input_diff> <output_patch> <hunk_indices_csv>

Example::

    python _split_diff.py host_diff.txt host_tier3.patch 2,7,9,10

Hunk indices are 1-based and match the order printed by
``_diff_hunks.py``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    keep = sorted({int(x) for x in sys.argv[3].split(",") if x.strip()})

    text = in_path.read_text(encoding="utf-8")
    if not text.startswith("diff --git "):
        print(f"[err] {in_path} does not look like a git diff (no diff --git header)")
        return 2

    # Split into per-file blocks. A patch can contain multiple files;
    # we currently target single-file diffs but keep this generic.
    file_blocks = re.split(r"(?m)^(?=diff --git )", text)
    file_blocks = [b for b in file_blocks if b.strip()]

    out_chunks: list[str] = []
    for fb in file_blocks:
        # Header is everything before the first ``@@`` line.
        m = re.search(r"(?m)^@@ ", fb)
        if not m:
            # No hunks (probably a pure rename / mode change). Pass through.
            out_chunks.append(fb)
            continue
        header = fb[: m.start()]
        body = fb[m.start() :]
        hunks = re.split(r"(?m)^(?=@@ )", body)
        hunks = [h for h in hunks if h.strip()]

        kept = [h for i, h in enumerate(hunks, 1) if i in keep]
        if not kept:
            continue

        out_chunks.append(header + "".join(kept))

    out = "".join(out_chunks)
    if not out.endswith("\n"):
        out += "\n"
    out_path.write_text(out, encoding="utf-8", newline="\n")
    print(
        f"[ok] wrote {out_path} ({len(out)} bytes, "
        f"{len(out.splitlines())} lines, kept hunks={keep})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
