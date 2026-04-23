import re
from pathlib import Path

rows = []
for line in Path("D:/Workspace/Pip-Boy/scripts/_importtime.txt").read_text(encoding="utf-8").splitlines():
    m = re.match(r"import time:\s+(\d+)\s+\|\s+(\d+)\s+\|\s*(.*)", line)
    if m:
        rows.append((int(m[1]), int(m[2]), m[3]))

# Only keep direct top-level package roots (no leading whitespace in module name).
top_only = [r for r in rows if not r[2].startswith(" ") and "." not in r[2].strip().split()[0]]
top_only.sort(key=lambda r: -r[1])
print("Top 25 top-level imports (cumulative):")
for self_us, cum_us, mod in top_only[:25]:
    print(f"  {cum_us/1000:8.1f} ms cum  {self_us/1000:7.1f} ms self   {mod}")

print("\nTop 25 ALL imports (cumulative):")
rows.sort(key=lambda r: -r[1])
for self_us, cum_us, mod in rows[:25]:
    print(f"  {cum_us/1000:8.1f} ms cum  {self_us/1000:7.1f} ms self   {mod}")
