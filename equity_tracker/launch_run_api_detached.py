"""Launch run_api.py as a detached background process on Windows."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    app_dir = Path(__file__).resolve().parent
    repo_root = app_dir.parent
    log_dir = repo_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = log_dir / "equity_tracker.out.log"
    stderr_log = log_dir / "equity_tracker.err.log"

    creationflags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creationflags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP

    with open(stdout_log, "ab") as stdout_handle, open(stderr_log, "ab") as stderr_handle:
        process = subprocess.Popen(
            [sys.executable, "run_api.py"],
            cwd=str(app_dir),
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creationflags,
            close_fds=True,
        )

    print(process.pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
