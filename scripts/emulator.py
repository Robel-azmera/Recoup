"""Start the Firebase emulators, keeping data across restarts.

Imports `.emulator-data/` if a previous export exists, and exports there on a clean shutdown
(Ctrl+C). Delete the folder, or re-run scripts/seed.py, for a fresh start.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".emulator-data"


def main() -> int:
    firebase = shutil.which("firebase") or shutil.which("firebase.cmd")
    if not firebase:
        print("firebase-tools not found: npm i -g firebase-tools")
        return 2
    cmd = [firebase, "emulators:start", "--only", "firestore,auth", "--project", "demo-recoup",
           f"--export-on-exit={DATA}"]
    if (DATA / "firebase-export-metadata.json").exists():
        cmd.append(f"--import={DATA}")
        print(f"Importing emulator data from {DATA}", flush=True)
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
