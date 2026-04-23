"""Bench the cost of importing the host graph.

Ten cold-starts of ``import pip_agent.agent_host`` (each in its own
interpreter) so we can see the Tier 3 delta without running the full
selftest harness every time.
"""

import subprocess
import sys
import time
from pathlib import Path

VENV_PY = Path(r"D:/Workspace/pip-test/.venv/Scripts/python.exe")
CODE = "import time; t=time.perf_counter(); import pip_agent.agent_host; print(f'{(time.perf_counter()-t)*1000:.1f}')"


def main() -> None:
    samples = []
    for _ in range(6):
        t0 = time.perf_counter()
        proc = subprocess.run(
            [str(VENV_PY), "-c", CODE],
            capture_output=True, text=True, timeout=30,
            cwd=r"D:/Workspace/pip-test",
        )
        wall = (time.perf_counter() - t0) * 1000
        imp = float(proc.stdout.strip()) if proc.stdout.strip() else float("nan")
        samples.append((wall, imp))
        print(f"  wall={wall:7.1f} ms  import={imp:7.1f} ms  rc={proc.returncode}")
    import_only = sorted(s[1] for s in samples)
    print(
        f"\nimport-only median = {import_only[len(import_only)//2]:.1f} ms  "
        f"min = {import_only[0]:.1f} ms  max = {import_only[-1]:.1f} ms"
    )


if __name__ == "__main__":
    main()
