"""Print every @@ hunk header from the saved diff so we can plan the
per-tier git-add-p mapping by hand."""
import re, sys, pathlib

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
hunks = re.split(r"(?m)^(?=@@ )", text)
print(f"TOTAL hunks: {len(hunks) - 1}\n")
for i, h in enumerate(hunks[1:], 1):
    header = h.splitlines()[0] if h.strip() else ""
    body_lines = h.splitlines()[:8]
    print(f"--- HUNK {i}: {header}")
    for line in body_lines[1:]:
        print(f"   {line}")
    print()
